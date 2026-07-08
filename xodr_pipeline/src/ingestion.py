import overturemaps
import json
import shapely.wkt
import shapely.wkb
import urllib.request
import urllib.parse
import traceback
from typing import Dict, Any, Tuple

def validate_bbox(bbox: Tuple[float, float, float, float]):
    """Validates that the bbox has correct types, ranges, and layout."""
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        raise ValueError(f"Bounding box must be a tuple/list of 4 coordinates, got {bbox}")
    
    try:
        min_lon, min_lat, max_lon, max_lat = map(float, bbox)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Bounding box coordinates must be numeric: {e}")
        
    if not (-180.0 <= min_lon <= 180.0) or not (-180.0 <= max_lon <= 180.0):
        raise ValueError(f"Longitude must be between -180 and 180. Got lon range: {min_lon} to {max_lon}")
    if not (-90.0 <= min_lat <= 90.0) or not (-90.0 <= max_lat <= 90.0):
        raise ValueError(f"Latitude must be between -90 and 90. Got lat range: {min_lat} to {max_lat}")
        
    if min_lon >= max_lon:
        raise ValueError(f"Minimum longitude ({min_lon}) must be less than maximum longitude ({max_lon})")
    if min_lat >= max_lat:
        raise ValueError(f"Minimum latitude ({min_lat}) must be less than maximum latitude ({max_lat})")

def fetch_osm_data(bbox: Tuple[float, float, float, float]) -> Dict[str, Any]:
    """
    Fetches OSM data for a given bounding box (min_lon, min_lat, max_lon, max_lat)
    using the Overpass API.
    """
    print(f"\n--- [Ingestion] Starting OSM Data Fetch ---")
    validate_bbox(bbox)
    min_lon, min_lat, max_lon, max_lat = bbox
    
    # Overpass bounding box is (min_lat, min_lon, max_lat, max_lon)
    # Filter ways to only pull drivable highway classes (excluding footways, railways, etc.)
    query = f"""
    [out:json][timeout:25];
    (
      node({min_lat},{min_lon},{max_lat},{max_lon});
      way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service|living_street|unknown)$"]({min_lat},{min_lon},{max_lat},{max_lon});
    );
    out body;
    """
    
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://z.overpass-api.de/api/interpreter"
    ]
    
    result = None
    last_error = None
    
    for url in endpoints:
        print(f"[Ingestion] Sending query to Overpass API at {url}...")
        print(f"[Ingestion] Query details:\n{query.strip()}")
        
        data = urllib.parse.urlencode({'data': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'OpenDRIVE-Generator-Pipeline/1.0'})
        
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                print("[Ingestion] Connection established, downloading OSM response...")
                result = json.loads(response.read().decode('utf-8'))
                print("[Ingestion] OSM data downloaded successfully.")
                break
        except Exception as e:
            print(f"[Ingestion] Warning: OSM fetch failed at {url}: {e}")
            last_error = e
            
    if result is None:
        print(f"[Ingestion] ERROR: All OSM endpoints failed!")
        if last_error:
            # Print traceback of last error to console
            traceback.print_exception(type(last_error), last_error, last_error.__traceback__)
        raise RuntimeError(f"Failed to fetch OSM data from all Overpass API endpoints. Last error: {last_error}")
    
    nodes = []
    ways = []
    
    elements = result.get('elements', [])
    for element in elements:
        if element['type'] == 'node':
            nodes.append({
                "id": str(element['id']),
                "lon": float(element['lon']),
                "lat": float(element['lat']),
                "tags": element.get('tags', {})
            })
        elif element['type'] == 'way':
            ways.append({
                "id": str(element['id']),
                "nodes": [str(n) for n in element.get('nodes', [])],
                "tags": element.get('tags', {})
            })
        
    print(f"[Ingestion] Parsed {len(nodes)} nodes and {len(ways)} ways from OSM data.")
    return {
        "nodes": nodes,
        "ways": ways
    }

def fetch_overture_data(bbox: Tuple[float, float, float, float]) -> Dict[str, Any]:
    """
    Fetches Overture segments and connectors for a bounding box using overturemaps.
    """
    print(f"\n--- [Ingestion] Starting Overture Maps Data Fetch ---")
    validate_bbox(bbox)
    min_lon, min_lat, max_lon, max_lat = bbox
    
    # 1. Fetch Segments
    print(f"[Ingestion] Fetching Overture 'segment' records for bbox={bbox} from AWS S3...")
    try:
        reader = overturemaps.core.record_batch_reader("segment", bbox=(min_lon, min_lat, max_lon, max_lat))
    except Exception as e:
        print(f"[Ingestion] ERROR: Failed to initialize Overture segment reader!")
        traceback.print_exc()
        raise RuntimeError(f"Failed to initialize Overture segment reader: {e}")
        
    ALLOWED_ROAD_CLASSES = {
        'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
        'residential', 'unclassified', 'service', 'living_street', 'unknown'
    }

    segments = []
    if reader is not None:
        try:
            for batch in reader:
                for row in batch.to_pylist():
                    geom = row.get("geometry")
                    if geom:
                        try:
                            # ── Semantic Filter: only keep drivable road classes ──
                            road_class = row.get('class')
                            if road_class not in ALLOWED_ROAD_CLASSES:
                                continue
                                
                            ls = shapely.wkb.loads(geom)
                            if ls.geom_type == 'LineString':
                                coords = list(ls.coords)
                                segments.append({
                                    "id": str(row.get('id')),
                                    "geometry": {"coordinates": coords},
                                    "connectors": row.get('connectors', []),
                                    "width_rules": row.get('width_rules', []),
                                    "class": row.get('class'),
                                    "subclass": row.get('subclass'),
                                    "sources": row.get('sources', [])
                                })
                        except Exception as parse_err:
                            print(f"[Ingestion] Warning: Failed to parse segment geometry WKB: {parse_err}")
        except Exception as e:
            print(f"[Ingestion] ERROR: Error during Overture segment download loop!")
            traceback.print_exc()
            raise RuntimeError(f"Error downloading Overture segments: {e}")
            
    print(f"[Ingestion] Retrieved {len(segments)} segment records.")

    # 2. Fetch Connectors
    print(f"[Ingestion] Fetching Overture 'connector' records for bbox={bbox} from AWS S3...")
    try:
        conn_reader = overturemaps.core.record_batch_reader("connector", bbox=(min_lon, min_lat, max_lon, max_lat))
    except Exception as e:
        print(f"[Ingestion] ERROR: Failed to initialize Overture connector reader!")
        traceback.print_exc()
        raise RuntimeError(f"Failed to initialize Overture connector reader: {e}")
        
    connectors = []
    if conn_reader is not None:
        try:
            for batch in conn_reader:
                for row in batch.to_pylist():
                    geom = row.get("geometry")
                    if geom:
                        try:
                            pt = shapely.wkb.loads(geom)
                            if pt.geom_type == 'Point':
                                connectors.append({
                                    "id": str(row.get('id')),
                                    "geometry": {"coordinates": [pt.x, pt.y]}
                                })
                        except Exception as parse_err:
                            print(f"[Ingestion] Warning: Failed to parse connector geometry WKB: {parse_err}")
        except Exception as e:
            print(f"[Ingestion] ERROR: Error during Overture connector download loop!")
            traceback.print_exc()
            raise RuntimeError(f"Error downloading Overture connectors: {e}")
            
    print(f"[Ingestion] Retrieved {len(connectors)} connector records.")

    if not segments:
        raise RuntimeError(f"No Overture segment records found in bounding box: {bbox}. Cannot generate OpenDRIVE without road segments.")
        
    return {
        "connectors": connectors,
        "segments": segments
    }
