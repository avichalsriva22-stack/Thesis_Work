from shapely.geometry import Point, LineString
from typing import Dict, Tuple, Optional, List
import math
import numpy as np
import traceback

from .models import (
    AnchoredOvertureSegment,
    AnchoredOSMNode,
    AnchoredOSMWay,
    SemanticSignal,
    RoadNode
)
from .parsers import parse_traffic_signals

def project_to_frenet(point: Point, centerline: LineString) -> Tuple[float, float]:
    """
    Orthogonally projects a point onto a centerline to get Frenet (s, t) coordinates.
    s = longitudinal distance along the line.
    t = lateral distance (perpendicular) from the line.
    """
    if not isinstance(centerline, LineString) or centerline.is_empty or centerline.length < 1e-4:
        print("[Enrichment] Warning: Centerline is empty or too short. Returning (0,0) Frenet projection.")
        return 0.0, 0.0
        
    try:
        # 1. Project to get s (distance along the line)
        s = centerline.project(point)
        
        # 2. Get the exact coordinates on the line at distance s
        projected_pt = centerline.interpolate(s)
        
        # 3. Calculate absolute t (distance)
        t = point.distance(projected_pt)
        
        # 4. Determine side (sign of t). 
        # OpenDRIVE convention: left is positive t, right is negative t (relative to heading).
        # We find the tangent vector at s to determine left/right.
        # To do this safely, we grab points slightly before and after s.
        s_prev = max(0.0, s - 0.1)
        s_next = min(centerline.length, s + 0.1)
        
        pt_prev = centerline.interpolate(s_prev)
        pt_next = centerline.interpolate(s_next)
        
        # Tangent vector
        dx = pt_next.x - pt_prev.x
        dy = pt_next.y - pt_prev.y
        
        # Vector from projected point to the actual point
        vx = point.x - projected_pt.x
        vy = point.y - projected_pt.y
        
        # Cross product (dx*vy - dy*vx). 
        # If cross product > 0, the point is to the left (positive t).
        cross = (dx * vy) - (dy * vx)
        
        if cross < 0:
            t = -t
            
        return s, t
    except Exception as e:
        print(f"[Enrichment] Warning: Frenet projection failed: {e}. Returning (0.0, 0.0)")
        return 0.0, 0.0

def assign_signals_to_roads(osm_nodes: Dict[int, AnchoredOSMNode], roads: Dict[str, RoadNode], overture_segments: Dict[str, AnchoredOvertureSegment]) -> None:
    """
    Iterates over OSM nodes, finds semantic signals, maps them to the nearest Overture segment,
    calculates their (s,t) offsets, and attaches them to the generated RoadNodes.
    """
    print(f"\n--- [Enrichment] Assigning Semantic Signals ---")
    print(f"[Enrichment] Processing {len(osm_nodes)} OSM nodes...")
    
    matched_count = 0
    ignored_count = 0
    
    for node_id, node in osm_nodes.items():
        try:
            signal_type = parse_traffic_signals(node.tags)
            if not signal_type:
                continue
                
            closest_seg_id = None
            min_dist = float('inf')
            
            for seg_id, seg in overture_segments.items():
                dist = seg.geometry.distance(node.geometry)
                if dist < min_dist:
                    min_dist = dist
                    closest_seg_id = seg_id
                    
            if closest_seg_id is None or min_dist > 50.0:
                # If it's too far away from any road (e.g. > 50 meters), ignore it
                ignored_count += 1
                continue
                
            # Project onto the centerline
            seg_geometry = overture_segments[closest_seg_id].geometry
            s, t = project_to_frenet(node.geometry, seg_geometry)
            
            # Orientation can be inferred from 'direction' tags or simply set to '+' (facing direction of s)
            orientation = '+' 
            if 'direction' in node.tags and node.tags['direction'] == 'backward':
                orientation = '-'
                
            signal = SemanticSignal(
                id=str(node_id),
                s=s,
                t=t,
                type=signal_type,
                orientation=orientation
            )
            
            # Attach to the Road
            if closest_seg_id in roads:
                roads[closest_seg_id].signals.append(signal)
                matched_count += 1
            else:
                ignored_count += 1
        except Exception as e:
            print(f"[Enrichment] Warning: Failed to assign signal for node '{node_id}': {e}")
            ignored_count += 1
            
    print(f"[Enrichment] Signals mapping complete: Mapped {matched_count} signals, skipped {ignored_count} nodes.")

def assign_lane_semantics(
    osm_ways: Dict[int, "AnchoredOSMWay"],
    roads: Dict[str, RoadNode],
    overture_segments: Dict[str, AnchoredOvertureSegment]
) -> None:
    """
    O(N) lane semantics enrichment.

    Phase A — Build a class→LaneSemantics lookup from OSM ways (single pass).
    Phase B — Apply to Overture segments by road_class match (single pass).
    Phase C — Fallback for any road still untagged (single pass, Overture class table).
    """
    print(f"\n--- [Enrichment] Assigning Lane Semantics ---")
    print(f"[Enrichment] Processing {len(osm_ways)} OSM ways...")
    
    from .models import LaneSemantics
    from .parsers import parse_turn_lanes
    import math

    def _safe_int(s, default=0):
        try: return max(0, int(s))
        except: return default

    # STANDARD lane counts / widths per Overture road_class
    CLASS_FWD = {"motorway":3,"trunk":2,"primary":2,"secondary":1,
                 "tertiary":1,"residential":1,"service":1,"track":1,"path":1}
    CLASS_BWD = {"motorway":0,"trunk":0,"primary":1,"secondary":1,
                 "tertiary":1,"residential":1,"service":1,"track":0,"path":0}

    HIGHWAY_TO_CLASS = {
        "motorway":"motorway","motorway_link":"motorway",
        "trunk":"trunk","trunk_link":"trunk",
        "primary":"primary","primary_link":"primary",
        "secondary":"secondary","secondary_link":"secondary",
        "tertiary":"tertiary","tertiary_link":"tertiary",
        "residential":"residential","living_street":"residential",
        "service":"service","track":"track","path":"path",
        "unclassified":"residential",
        "rail":"rail", "narrow_gauge":"rail", "standard_gauge":"rail", "light_rail":"rail"
    }
    SKIP_HIGHWAY = {"footway","cycleway","path","steps","pedestrian","construction","proposed"}

    # ── Phase A: Build class → best LaneSemantics from OSM ways ───────────────
    class_semantics: Dict[str, "LaneSemantics"] = {}
    way_semantics: Dict[int, "LaneSemantics"] = {}

    for way_id, way in osm_ways.items():
        try:
            tags = way.tags
            highway = tags.get("highway", "")
            
            if not highway:
                continue
            if highway in SKIP_HIGHWAY:
                continue

            ov_class = HIGHWAY_TO_CLASS.get(highway, "residential")

            # Parse OSM width
            osm_width_m = 0.0
            width_str = tags.get("width") or tags.get("est_width")
            if width_str:
                try:
                    w_clean = width_str.strip().split()[0].replace("'", "")
                    osm_width_m = float(w_clean)
                    if "'" in width_str:
                        osm_width_m *= 0.3048
                    osm_width_m = max(2.0, min(osm_width_m, 50.0))
                except (ValueError, TypeError):
                    osm_width_m = 0.0

            oneway_tag = tags.get("oneway", "no").lower()
            if highway in ("motorway", "motorway_link", "trunk", "trunk_link"):
                is_oneway = oneway_tag not in ("no", "false", "0")
            else:
                is_oneway = oneway_tag in ("yes", "1", "true", "-1", "reverse")
                
            total_lanes   = _safe_int(tags.get("lanes"), 0)
            forward_lanes = _safe_int(tags.get("lanes:forward"), 0)
            backward_lanes= _safe_int(tags.get("lanes:backward"), 0)

            if total_lanes == 0 and forward_lanes == 0 and backward_lanes == 0:
                if ov_class not in class_semantics:
                    fwd = CLASS_FWD.get(ov_class, 1)
                    bwd = 0 if is_oneway else CLASS_BWD.get(ov_class, 1)
                    class_semantics[ov_class] = LaneSemantics(
                        turn_rules=["through"] * fwd,
                        n_forward=fwd, n_backward=bwd,
                        is_oneway=is_oneway, osm_width_m=osm_width_m,
                    )
                continue

            # Derive forward/backward split
            if total_lanes > 0 and forward_lanes == 0 and backward_lanes == 0:
                if is_oneway:
                    forward_lanes  = total_lanes
                    backward_lanes = 0
                else:
                    forward_lanes  = math.ceil(total_lanes / 2)
                    backward_lanes = total_lanes - forward_lanes
            elif total_lanes == 0:
                total_lanes = forward_lanes + backward_lanes

            forward_lanes  = max(1, forward_lanes)
            backward_lanes = max(0, backward_lanes)

            turn_lane_str = tags.get("turn:lanes") or tags.get("turn:lanes:forward", "")
            turn_rules = parse_turn_lanes(turn_lane_str) if turn_lane_str else ["through"] * forward_lanes

            sem = LaneSemantics(
                turn_rules=turn_rules,
                n_forward=forward_lanes, n_backward=backward_lanes,
                is_oneway=is_oneway, osm_width_m=osm_width_m,
                road_class=ov_class
            )

            way_semantics[way_id] = sem
        except Exception as e:
            print(f"[Enrichment] Warning: Failed to extract lane semantics for way '{way_id}': {e}")

    print(f"[Enrichment] Phase A: Formulated lane lookup profiles for {len(class_semantics)} road classes.")

    # ── Phase B: Apply OSM and class lookup to Overture segments ────────────────
    matched_from_osm = 0
    for seg_id, seg in overture_segments.items():
        if seg_id not in roads:
            continue
            
        osm_way_id = None
        for src in seg.sources:
            if src.get('dataset') == 'OpenStreetMap':
                rec_id = src.get('record_id', '')
                if rec_id.startswith('way/'):
                    try:
                        osm_way_id = int(rec_id.split('/')[1])
                        break
                    except: pass
                elif rec_id.startswith('w'):
                    try:
                        osm_way_id = int(rec_id[1:].split('@')[0])
                        break
                    except: pass

        matched_sem = None
        if osm_way_id is not None and osm_way_id in way_semantics:
            matched_sem = way_semantics[osm_way_id]
            
        if matched_sem is None:
            seg_class = (seg.road_class or "residential").lower()
            for key, sem in class_semantics.items():
                if key in seg_class:
                    matched_sem = sem
                    break

        if matched_sem is not None:
            roads[seg_id].lane_semantics = matched_sem
            matched_from_osm += 1
            


    print(f"[Enrichment] Phase B: Matched {matched_from_osm} Overture segments with OSM profiles.")

    # ── Phase C: Overture road_class fallback (for untagged roads) ────────────
    fallback_count = 0
    for seg_id, seg in overture_segments.items():
        if seg_id not in roads:
            continue
        if roads[seg_id].lane_semantics is not None:
            continue

        road_class = (seg.road_class or "residential").lower()
        matched_key = "residential"
        for key in CLASS_FWD:
            if key in road_class:
                matched_key = key
                break

        fwd = CLASS_FWD.get(matched_key, 1)
        bwd = CLASS_BWD.get(matched_key, 1)
        roads[seg_id].lane_semantics = LaneSemantics(
            turn_rules=["through"] * fwd,
            n_forward=fwd, n_backward=bwd,
            is_oneway=(bwd == 0), osm_width_m=0.0,
            road_class=road_class
        )
        fallback_count += 1

    print(f"[Enrichment] Phase C: Set default fallback semantics on {fallback_count} segments.")
    
    # ── Phase D: Dual Carriageway Detection & Enforcement ─────────────────────
    # Strategy 1: Class-based — motorways and trunks are always one-way
    # Strategy 2: Geometric — detect near-parallel, opposite-heading segment pairs
    # Strategy 3: Overture road_flags — check for divided/link indicators
    
    forced_oneway_count = 0
    
    # Strategy 1: Class-based enforcement (motorway/trunk always one-way)
    for seg_id, seg in overture_segments.items():
        if seg_id not in roads:
            continue
        if seg.road_class in ["motorway", "trunk", "motorway_link", "trunk_link"]:
            ls = roads[seg_id].lane_semantics
            if ls and ls.n_backward > 0:
                roads[seg_id].lane_semantics = LaneSemantics(
                    turn_rules=["through"] * ls.n_forward,
                    n_forward=ls.n_forward,
                    n_backward=0,
                    is_oneway=True,
                    osm_width_m=ls.osm_width_m,
                    road_class=ls.road_class
                )
                forced_oneway_count += 1
    
    print(f"[Enrichment] Phase D (Strategy 1): Forced {forced_oneway_count} motorway/trunk segments to one-way.")
    

    

    print(f"[Enrichment] Lane semantics assignment complete.")

