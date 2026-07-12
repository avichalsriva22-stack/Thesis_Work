from typing import Dict, Any
import urllib.request
import json
import logging
import time
import traceback

from .topology import RoadGraph
from .assembly import generate_xodr
from .validation import validate_xodr

def get_country_code(lon: float, lat: float) -> str:
    """Uses Nominatim to reverse geocode the center of the bounding box to an ISO 3166-1 alpha-2 country code."""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
        req = urllib.request.Request(url, headers={'User-Agent': 'xodr-pipeline-generator'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            country_code = data.get("address", {}).get("country_code", "US").upper()
            return country_code
    except Exception as e:
        logging.warning(f"Failed to reverse geocode country: {e}. Defaulting to 'US'.")
        return "US"

def run_pipeline(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    output_path: str = None
) -> str:
    """
    Orchestrates the 6 phases:
    Phase 1: Fetch OSM and Overture data for the bbox
    Phase 2: Project coordinates, generate math splines, build RoadGraph
    Phase 3: Parse OSM tags, project to (s,t) Frenet, enrich RoadGraph
    Phase 4: Compile RoadGraph to ASAM OpenDRIVE XML
    Phase 5: QC OpenDRIVE validation

    If output_path is given, the XML is written there before Phase 6 validation
    so qc_opendrive can read it from disk.
    """
    pipeline_start = time.time()
    bbox = (min_lon, min_lat, max_lon, max_lat)
    
    print(f"\n============================================================")
    print(f"   STARTING OPENDRIVE MAP GENERATION PIPELINE")
    print(f"   Bounding Box: {bbox}")
    print(f"============================================================")
    
    # --- PHASE 1: Fetch Live Data ---
    print(f"\n[Phase 1] Ingesting source geodata (OSM and Overture)...")
    phase_start = time.time()
    try:
        from .ingestion import fetch_overture_data, fetch_osm_data
        overture_raw = fetch_overture_data(bbox)
        osm_raw = fetch_osm_data(bbox)
        print(f"[Phase 1] Ingestion completed in {time.time() - phase_start:.2f} seconds.")
    except Exception as e:
        print(f"\n[ERROR] Pipeline failed in Phase 1 (Data Ingestion): {e}")
        traceback.print_exc()
        raise RuntimeError(f"Phase 1 Ingestion Error: {e}") from e

    # --- PHASE 2: Anchor to Local UTM ---
    print(f"\n[Phase 2] Anchoring spatial datasets to UTM grid...")
    phase_start = time.time()
    try:
        from .anchoring import get_utm_zone_epsg, CoordinateAnchor
        center_lon = (min_lon + max_lon) / 2.0
        center_lat = (min_lat + max_lat) / 2.0
        epsg = get_utm_zone_epsg(center_lon, center_lat)
        anchor = CoordinateAnchor(target_epsg=epsg)
        
        # Set the local origin (0,0) to exactly the center of the bounding box
        center_x, center_y = anchor.transformer.transform(center_lon, center_lat)
        anchor.offset_x = center_x
        anchor.offset_y = center_y
        anchor.set_clip_bbox_wgs84(min_lon, min_lat, max_lon, max_lat, buffer_m=100.0)
        
        overture_anchored = anchor.process_overture_data(overture_raw)
        osm_anchored = anchor.process_osm_data(osm_raw)
        print(f"[Phase 2] Spatial anchoring completed in {time.time() - phase_start:.2f} seconds.")
    except Exception as e:
        print(f"\n[ERROR] Pipeline failed in Phase 2 (UTM Anchoring): {e}")
        traceback.print_exc()
        raise RuntimeError(f"Phase 2 Anchoring Error: {e}") from e

    # --- PHASE 3: Build Topology and Geometry ---
    print(f"\n[Phase 3] Generating continuous planview splines and road topology...")
    phase_start = time.time()
    try:
        from .topology import build_topology, split_segments_at_connectors
        
        # Split continuous segments at intersections
        overture_anchored['segments'] = split_segments_at_connectors(overture_anchored['segments'])
        
        graph = build_topology(overture_anchored['segments'], overture_anchored['connectors'])
        print(f"[Phase 3] Topology & geometry fit completed in {time.time() - phase_start:.2f} seconds.")
    except Exception as e:
        print(f"\n[ERROR] Pipeline failed in Phase 3 (Topology/Geometry Fitting): {e}")
        traceback.print_exc()
        raise RuntimeError(f"Phase 3 Topology/Geometry Fitting Error: {e}") from e

    # --- PHASE 4: Enrich Semantic Signals ---
    print(f"\n[Phase 4] Aligning and projecting OSM semantic features onto backbone...")
    phase_start = time.time()
    try:
        from .enrichment import assign_signals_to_roads, assign_lane_semantics
        assign_signals_to_roads(osm_anchored['nodes'], graph.roads, overture_anchored['segments'])
        assign_lane_semantics(osm_anchored['ways'], graph.roads, overture_anchored['segments'])
        
        from .topology import resolve_junction_lane_connections
        resolve_junction_lane_connections(graph)
        
        from .junction_geometry import build_junction_connecting_roads
        connecting_roads = build_junction_connecting_roads(graph)
        graph.roads.update(connecting_roads)
        
        print(f"[Phase 4] Semantic enrichment and lane resolution completed in {time.time() - phase_start:.2f} seconds.")
    except Exception as e:
        print(f"\n[ERROR] Pipeline failed in Phase 4 (Semantic Enrichment): {e}")
        traceback.print_exc()
        raise RuntimeError(f"Phase 4 Semantic Enrichment Error: {e}") from e

    # --- PHASE 5: Compile OpenDRIVE XML ---
    print(f"\n[Phase 5] Compiling RoadGraph to ASAM OpenDRIVE object model...")
    phase_start = time.time()
    try:
        center_lon = (min_lon + max_lon) / 2.0
        center_lat = (min_lat + max_lat) / 2.0
        country_code = get_country_code(center_lon, center_lat)
        
        print(f"[Phase 5] Generating OpenDRIVE XML (Country Code: {country_code})...")
        xodr_xml_string = generate_xodr(graph, country_code=country_code)
        print(f"[Phase 5] Compilation and serialization completed in {time.time() - phase_start:.2f} seconds.")
    except Exception as e:
        print(f"\n[ERROR] Pipeline failed in Phase 5 (OpenDRIVE Compilation): {e}")
        traceback.print_exc()
        raise RuntimeError(f"Phase 5 Compilation Error: {e}") from e

    # --- PHASE 6: QC Validation (official qc_opendrive) ---
    print(f"\n[Phase 6] Validating serialized OpenDRIVE output with QC OpenDRIVE...")
    phase_start = time.time()
    try:
        # Write to disk first so qc_opendrive can read it directly
        if output_path:
            import os
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(xodr_xml_string)
            print(f"[Phase 6] Wrote XODR to {output_path} for QC validation.")
            validate_xodr(xodr_xml_string, xodr_file_path=output_path)
        else:
            validate_xodr(xodr_xml_string)
        print(f"[Phase 6] Validation completed in {time.time() - phase_start:.2f} seconds.")
    except Exception as e:
        print(f"\n[ERROR] Pipeline failed in Phase 6 (QC Validation): {e}")
        traceback.print_exc()
        raise RuntimeError(f"Phase 6 Validation Error: {e}") from e

    total_duration = time.time() - pipeline_start
    print(f"\n============================================================")
    print(f"   OPENDRIVE GENERATION SUCCESSFUL")
    print(f"   Total duration: {total_duration:.2f} seconds")
    print(f"============================================================\n")
    
    return xodr_xml_string
