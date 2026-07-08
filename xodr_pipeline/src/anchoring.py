import math
from pyproj import CRS, Transformer
from shapely.geometry import Point, LineString, box as shapely_box
from typing import List, Dict, Tuple, Any, Optional

from .models import (
    AnchoredOvertureConnector,
    SegmentConnectorRef,
    AnchoredOvertureSegment,
    AnchoredOSMNode,
    AnchoredOSMWay,
)

def get_utm_zone_epsg(lon: float, lat: float) -> int:
    """
    Calculate the EPSG code of the UTM zone for a given longitude and latitude.
    """
    zone_number = math.floor((lon + 180) / 6) + 1
    
    if lat >= 0:
        # Northern Hemisphere
        return 32600 + zone_number
    else:
        # Southern Hemisphere
        return 32700 + zone_number

class CoordinateAnchor:
    def __init__(self, target_epsg: int):
        self.target_epsg = target_epsg
        # Pyproj transformer: EPSG:4326 (WGS84 lat/lon) to target UTM
        self.transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{target_epsg}", always_xy=True)
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.clip_box: Optional[LineString] = None  # shapely box for clipping

    def set_clip_bbox_wgs84(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float, buffer_m: float = 100.0):
        """
        Set a clipping bounding box from WGS84 coords.  Segments that extend
        far beyond the requested area will be clipped to this box (plus a buffer
        in metres) so that viewers can render the result at a sensible zoom.
        """
        x1, y1 = self.transformer.transform(min_lon, min_lat)
        x2, y2 = self.transformer.transform(max_lon, max_lat)
        self.clip_box = shapely_box(
            min(x1, x2) - self.offset_x - buffer_m,
            min(y1, y2) - self.offset_y - buffer_m,
            max(x1, x2) - self.offset_x + buffer_m,
            max(y1, y2) - self.offset_y + buffer_m,
        )

    def project_point(self, lon: float, lat: float) -> Point:
        x, y = self.transformer.transform(lon, lat)
        return Point(x - self.offset_x, y - self.offset_y)

    def project_linestring(self, coords: List[Tuple[float, float]]) -> LineString:
        projected_coords = []
        for lon, lat in coords:
            x, y = self.transformer.transform(lon, lat)
            projected_coords.append((x - self.offset_x, y - self.offset_y))
        return LineString(projected_coords)

    def process_overture_data(self, overture_data: Dict[str, Any]) -> Dict[str, Any]:
        print(f"\n--- [Anchoring] Anchoring Overture Data to UTM EPSG:{self.target_epsg} ---")
        print(f"[Anchoring] Local Offset X={self.offset_x:.3f}, Y={self.offset_y:.3f}")
        
        anchored_connectors = {}
        anchored_segments = {}

        connectors_raw = overture_data.get('connectors', [])
        segments_raw = overture_data.get('segments', [])
        
        if not connectors_raw and not segments_raw:
            print("[Anchoring] Warning: Overture data is empty.")
            
        for conn in connectors_raw:
            try:
                geom = conn.get('geometry')
                if not geom or 'coordinates' not in geom or len(geom['coordinates']) < 2:
                    raise ValueError(f"Invalid or missing coordinates in connector {conn.get('id')}")
                
                coords = geom['coordinates']
                pt = self.project_point(coords[0], coords[1])
                anchored_connectors[conn['id']] = AnchoredOvertureConnector(id=conn['id'], geometry=pt)
            except Exception as e:
                print(f"[Anchoring] Warning: Failed to anchor connector {conn.get('id', 'unknown')}: {e}")
                
        for seg in segments_raw:
            try:
                geom = seg.get('geometry')
                if not geom or 'coordinates' not in geom or len(geom['coordinates']) < 2:
                    raise ValueError(f"Invalid or missing coordinates in segment {seg.get('id')}")
                
                coords = geom['coordinates']
                ls = self.project_linestring(coords)

                # Clip the segment geometry to the bounding box so that roads
                # extending far beyond the requested area don't bloat the map.
                if self.clip_box is not None:
                    clipped = ls.intersection(self.clip_box)
                    if clipped.is_empty:
                        continue  # entirely outside the bbox — skip
                    # intersection may return a MultiLineString; keep the longest piece
                    if clipped.geom_type == 'MultiLineString':
                        clipped = max(clipped.geoms, key=lambda g: g.length)
                    if clipped.geom_type != 'LineString' or clipped.length < 0.1:
                        continue
                    ls = clipped

                conns = []
                for c in seg.get('connectors', []):
                    c_id = c.get('connector_id')
                    if c_id:
                        conns.append(SegmentConnectorRef(id=c_id, at=c.get('at', 0.0)))
                
                anchored_segments[seg['id']] = AnchoredOvertureSegment(
                    id=seg['id'],
                    geometry=ls,
                    connectors=conns,
                    width_rules=seg.get('width_rules', []),
                    subtype=seg.get('subtype'),
                    road_class=seg.get('class'),
                    subclass=seg.get('subclass'),
                    sources=seg.get('sources', [])
                )
            except Exception as e:
                print(f"[Anchoring] Warning: Failed to anchor segment {seg.get('id', 'unknown')}: {e}")

        print(f"[Anchoring] Successfully anchored {len(anchored_connectors)} connectors and {len(anchored_segments)} segments.")
        return {
            "connectors": anchored_connectors,
            "segments": anchored_segments
        }

    def process_osm_data(self, osm_data: Dict[str, Any], is_scaled_1e7: bool = False) -> Dict[str, Any]:
        print(f"\n--- [Anchoring] Anchoring OSM Data to UTM EPSG:{self.target_epsg} ---")
        
        anchored_nodes = {}
        anchored_ways = {}

        nodes_raw = osm_data.get('nodes', [])
        ways_raw = osm_data.get('ways', [])
        
        if not nodes_raw and not ways_raw:
            print("[Anchoring] Warning: OSM data is empty.")
            
        for node in nodes_raw:
            try:
                lon = node.get('lon')
                lat = node.get('lat')
                if lon is None or lat is None:
                    raise ValueError(f"Missing lat/lon for node {node.get('id')}")
                
                if is_scaled_1e7:
                    lon = lon / 1e7
                    lat = lat / 1e7
                    
                pt = self.project_point(lon, lat)
                anchored_nodes[node['id']] = AnchoredOSMNode(
                    id=node['id'],
                    version=node.get('version', 1),
                    geometry=pt,
                    tags=node.get('tags', {})
                )
            except Exception as e:
                print(f"[Anchoring] Warning: Failed to anchor node {node.get('id', 'unknown')}: {e}")

        for way in ways_raw:
            # Ways just hold lists of node IDs, no coordinates to project here
            try:
                way_id = way.get('id')
                if way_id is None:
                    raise ValueError("Way missing 'id' field")
                anchored_ways[way_id] = AnchoredOSMWay(
                    id=way_id,
                    version=way.get('version', 1),
                    nodes=way.get('nodes', []),
                    tags=way.get('tags', {})
                )
            except Exception as e:
                print(f"[Anchoring] Warning: Failed to process way {way.get('id', 'unknown')}: {e}")

        print(f"[Anchoring] Successfully anchored {len(anchored_nodes)} nodes and processed {len(anchored_ways)} ways.")
        return {
            "nodes": anchored_nodes,
            "ways": anchored_ways
        }
