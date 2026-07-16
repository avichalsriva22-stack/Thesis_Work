from typing import Dict, List, Optional
import math
from shapely.ops import substring
from .models import (
    AnchoredOvertureSegment,
    AnchoredOvertureConnector,
    SegmentConnectorRef,
    RoadNode,
    JunctionNode,
    RoadLink,
    JunctionConnection,
    LaneLink,
    PlanViewGeometry
)
from .geometry import segment_to_planview

class RoadGraph:
    def __init__(self):
        self.roads: Dict[str, RoadNode] = {}
        self.junctions: Dict[str, JunctionNode] = {}

def split_segments_at_connectors(segments: Dict[str, AnchoredOvertureSegment]) -> Dict[str, AnchoredOvertureSegment]:
    """Splits Overture segments that have connectors strictly inside them (0 < at < 1) into multiple segments."""
    print(f"\n--- [Topology] Splitting continuous segments at intersections ---")
    new_segments = {}
    split_count = 0
    
    for seg_id, seg in segments.items():
        split_points = {0.0, 1.0}
        for c in seg.connectors:
            split_points.add(c.at)
        
        split_points = sorted(list(split_points))
        
        if len(split_points) == 2:
            new_segments[seg_id] = seg
            continue
            
        split_count += 1
        for i in range(len(split_points) - 1):
            start_at = split_points[i]
            end_at = split_points[i+1]
            if end_at - start_at < 1e-4:
                continue
                
            new_id = f"{seg_id}_p{i}"
            new_geom = substring(seg.geometry, start_at, end_at, normalized=True)
            
            new_connectors = []
            for c in seg.connectors:
                if abs(c.at - start_at) < 1e-4:
                    new_connectors.append(SegmentConnectorRef(id=c.id, at=0.0))
                elif abs(c.at - end_at) < 1e-4:
                    new_connectors.append(SegmentConnectorRef(id=c.id, at=1.0))
            
            new_seg = AnchoredOvertureSegment(
                id=new_id, 
                geometry=new_geom, 
                connectors=new_connectors,
                width_rules=seg.width_rules,
                subtype=seg.subtype,
                road_class=seg.road_class,
                subclass=seg.subclass,
                sources=seg.sources
            )
            new_segments[new_id] = new_seg
            
    print(f"[Topology] Split {split_count} segments into smaller junction-bounded roads.")
    return new_segments

def build_topology(segments: Dict[str, AnchoredOvertureSegment], connectors: Dict[str, AnchoredOvertureConnector]) -> RoadGraph:
    print(f"\n--- [Topology] Building Road Topology ---")
    print(f"[Topology] Processing {len(segments)} segments and {len(connectors)} connectors...")
    
    graph = RoadGraph()
    
    if not segments:
        print("[Topology] Warning: No segments provided to topology builder.")
        return graph

    # 1. Map connectors to segments connecting to them
    # Initialize with known connectors
    connector_to_segments: Dict[str, List[Dict]] = {c_id: [] for c_id in connectors}
    
    for seg_id, seg in segments.items():
        # First, generate the geometry
        try:
            planview = segment_to_planview(seg)
        except Exception as e:
            print(f"[Topology] ERROR: Failed to compute planview geometry for segment {seg_id}: {e}")
            raise
            
        road = RoadNode(
            id=seg_id,
            geometry=planview,
            lane_widths=seg.width_rules
        )
        graph.roads[seg_id] = road
        
        for conn_ref in seg.connectors:
            if conn_ref.id in connector_to_segments:
                connector_to_segments[conn_ref.id].append({
                    "segment_id": seg_id,
                    "at": conn_ref.at
                })
            else:
                print(f"[Topology] Warning: Segment '{seg_id}' references connector '{conn_ref.id}' missing from connectors dictionary. Creating topological placeholder.")
                connector_to_segments[conn_ref.id] = [{
                    "segment_id": seg_id,
                    "at": conn_ref.at
                }]

    # 2. Build linkages and junctions
    for conn_id, connected_segs in connector_to_segments.items():
        if len(connected_segs) == 0:
            continue
            
        elif len(connected_segs) == 2:
            # Simple direct connection between two roads (predecessor/successor)
            seg_a = connected_segs[0]
            seg_b = connected_segs[1]
            
            _wire_direct_connection(graph, seg_a, seg_b, conn_id)
            
        elif len(connected_segs) > 2:
            # Complex junction
            junction = JunctionNode(id=conn_id)
            graph.junctions[conn_id] = junction
            
            for c_seg in connected_segs:
                seg_id = c_seg["segment_id"]
                is_incoming = c_seg["at"] > 0.5
                contact = "end" if is_incoming else "start"
                
                # Update the road to point to the junction
                link = RoadLink(element_type="junction", element_id=conn_id, contact_point=contact)
                if is_incoming:
                    graph.roads[seg_id].successor = link
                else:
                    graph.roads[seg_id].predecessor = link
                
                junction.connected_roads.append((seg_id, contact))

            # NOTE: junction.connections is deliberately left EMPTY here.
            # It is populated later by resolve_junction_lane_connections(),
            # once lane_semantics has been assigned (Phase 4).

    print(f"[Topology] Topology generation complete. Created {len(graph.roads)} roads and {len(graph.junctions)} junctions.")
    return graph

def _wire_direct_connection(graph: RoadGraph, seg_a: Dict, seg_b: Dict, conn_id: str):
    """
    Wires two segments together as predecessor/successor based on their 'at' value.
    'at' ~ 0 means start of segment. 'at' ~ 1 means end of segment.
    """
    # Determine contact points
    a_contact = "start" if seg_a["at"] < 0.5 else "end"
    b_contact = "start" if seg_b["at"] < 0.5 else "end"
    
    a_id = seg_a["segment_id"]
    b_id = seg_b["segment_id"]
    
    # Wire A to B
    link_to_b = RoadLink(element_type="road", element_id=b_id, contact_point=b_contact)
    if a_contact == "start":
        graph.roads[a_id].predecessor = link_to_b
    else:
        graph.roads[a_id].successor = link_to_b
        
    # Wire B to A
    link_to_a = RoadLink(element_type="road", element_id=a_id, contact_point=a_contact)
    if b_contact == "start":
        graph.roads[b_id].predecessor = link_to_a
    else:
        graph.roads[b_id].successor = link_to_a

def _primitive_end_pose(p: "GeometricPrimitive") -> tuple[float, float, float]:
    from .models import ArcPrimitive
    if isinstance(p, ArcPrimitive):
        if abs(p.curvature) > 1e-6:
            end_hdg = (p.heading + p.length * p.curvature) % (2 * math.pi)
            x = p.x + (math.sin(end_hdg) - math.sin(p.heading)) / p.curvature
            y = p.y - (math.cos(end_hdg) - math.cos(p.heading)) / p.curvature
            return x, y, end_hdg
    # Fallback for LinePrimitive or near-zero curvature
    end_hdg = p.heading % (2 * math.pi)
    x = p.x + p.length * math.cos(end_hdg)
    y = p.y + p.length * math.sin(end_hdg)
    return x, y, end_hdg

def _heading_at_connector(road: "RoadNode", contact: str) -> float:
    """
    Returns the direction of travel (radians) at the point where `road`
    touches the junction, as a vector pointing AWAY from the junction
    into the road (used purely for angular sorting of roads around a node).
    """
    prims = road.geometry.primitives
    if not prims:
        return 0.0
    if contact == "start":
        # Road leaves the junction from its start; heading already points away.
        return prims[0].heading
    else:
        # Road arrives at the junction at its end; flip 180 deg to point
        # away from the junction, back along the road.
        _, _, hdg = _primitive_end_pose(prims[-1])
        return (hdg + math.pi) % (2 * math.pi)

def _endpoint_xy(road: "RoadNode", contact: str, lateral_offset: float = 0.0) -> tuple[float, float]:
    prims = road.geometry.primitives
    if contact == "start":
        p = prims[0]
        # Normal vector pointing left is heading + pi/2
        x = p.x + lateral_offset * math.cos(p.heading + math.pi/2)
        y = p.y + lateral_offset * math.sin(p.heading + math.pi/2)
        return (x, y)
    else:
        p = prims[-1]
        x, y, hdg = _primitive_end_pose(p)
        # Normal vector pointing left is heading + pi/2
        x += lateral_offset * math.cos(hdg + math.pi/2)
        y += lateral_offset * math.sin(hdg + math.pi/2)
        return (x, y)

def _heading_into_junction(road: "RoadNode", contact: str) -> float:
    """
    Heading pointing INTO the junction as a vehicle on `road` approaches it
    — this is the convention pyclothoids.SolveG2 / CommonJunctionCreator use
    for the tangent angle at each endpoint.
    """
    prims = road.geometry.primitives
    if contact == "start":
        # road leaves the junction from its start; heading "into" the
        # junction is the reverse of the road's own forward heading here.
        return (prims[0].heading + math.pi) % (2 * math.pi)
    else:
        # road arrives at the junction at its end; its own forward heading
        # already points toward/into the junction.
        _, _, hdg = _primitive_end_pose(prims[-1])
        return hdg % (2 * math.pi)

def _turn_angle(inc_heading: float, out_heading: float) -> float:
    """
    Signed turn angle in degrees between an incoming road's heading
    (direction traffic is travelling as it enters the node) and an
    outgoing road's heading (direction traffic must travel to leave on it).
    0 = straight through, +90 = right turn, -90 = left turn
    """
    diff = math.degrees(out_heading - inc_heading)
    diff = (diff + 180) % 360 - 180  # normalize to [-180, 180)
    return diff

MAX_TURN_ANGLE_DEG = 150.0   # exclude near-U-turns as invalid movements

def resolve_junction_lane_connections(graph: "RoadGraph", is_lht: bool = False, viewer_safe: bool = False) -> None:
    """
    Must be called AFTER assign_lane_semantics() has populated
    RoadNode.lane_semantics for every road.
    Properly handles both forward and backward lanes on two-way roads.
    """
    for junction in graph.junctions.values():
        incoming = [] # List of (seg_id, incoming_direction, heading_into_junction, n_lanes)
        outgoing = [] # List of (seg_id, outgoing_direction, heading_away_from_junction, n_lanes)
        
        for seg_id, contact in junction.connected_roads:
            road = graph.roads[seg_id]
            sem = road.lane_semantics
            if sem is None:
                continue
                
            heading_away_end = _heading_at_connector(road, "end")
            heading_away_start = _heading_at_connector(road, "start")
            
            # Forward lanes flow from start to end. Backward lanes flow from end to start.
            if contact == "end":
                if sem.n_forward > 0:
                    incoming.append((seg_id, -1, (heading_away_end + math.pi) % (2 * math.pi), sem.n_forward))
                if sem.n_backward > 0:
                    outgoing.append((seg_id, 1, heading_away_end, sem.n_backward))
            else: # contact == "start"
                if sem.n_forward > 0:
                    outgoing.append((seg_id, -1, heading_away_start, sem.n_forward))
                if sem.n_backward > 0:
                    incoming.append((seg_id, 1, (heading_away_start + math.pi) % (2 * math.pi), sem.n_backward))
                    
        for inc_id, inc_dir, inc_heading, inc_lanes in incoming:
            candidates = []
            for out_id, out_dir, out_heading, out_lanes in outgoing:
                if out_id == inc_id:
                    continue  # never connect a road back to itself
                
                angle = _turn_angle(inc_heading, out_heading)
                if abs(angle) > MAX_TURN_ANGLE_DEG:
                    continue  # excludes implausible U-turn-style connections
                candidates.append((abs(angle), out_id, out_dir, angle, out_lanes))

            if not candidates:
                continue

            candidates.sort(key=lambda c: c[0])  # closest-to-straight first

            for _, out_id, out_dir, angle, out_lanes in candidates:
                n_lanes = min(inc_lanes, out_lanes)
                if n_lanes < 1:
                    continue
                
                # Determine mapping based on angle.
                # Left turn (angle < -20) or Straight (-20 to 20): Use innermost lanes (index 0..n_lanes-1)
                # Right turn (angle > 20): Use outermost lanes (index total_lanes-n_lanes..total_lanes-1)
                if angle > 20: # Right turn
                    inc_start_idx = inc_lanes - n_lanes
                    out_start_idx = out_lanes - n_lanes
                else:          # Left or Straight
                    inc_start_idx = 0
                    out_start_idx = 0
                
                # Lanes are indexed 1..N. The outermost is N. The innermost is 1.
                lane_links = []
                for i in range(n_lanes):
                    inc_lane_abs = inc_start_idx + i + 1
                    out_lane_abs = out_start_idx + i + 1
                    
                    # Forward lanes map to negative IDs, backward to positive
                    if is_lht and not viewer_safe:
                        from_l = inc_lane_abs if inc_dir == -1 else -inc_lane_abs
                        to_l = out_lane_abs if out_dir == -1 else -out_lane_abs
                    else:
                        from_l = -inc_lane_abs if inc_dir == -1 else inc_lane_abs
                        to_l = -out_lane_abs if out_dir == -1 else out_lane_abs
                        
                    lane_links.append(LaneLink(from_lane=from_l, to_lane=to_l))

                # Calculate lateral physical offsets to properly align the connecting road
                # The connecting road's reference line is anchored to the LEFT edge of the INNERMOST mapped lane.
                # If inc_lanes are on the right (-), offset is negative. If on the left (+), offset is positive.
                # offset = sign * (abs(innermost) - 1) * lane_width
                inc_sem = graph.roads[inc_id].lane_semantics
                out_sem = graph.roads[out_id].lane_semantics
                
                inc_total_lanes = (inc_sem.n_forward + inc_sem.n_backward) if inc_sem else 1
                out_total_lanes = (out_sem.n_forward + out_sem.n_backward) if out_sem else 1
                
                inc_lane_width = (inc_sem.osm_width_m / inc_total_lanes) if (inc_sem and inc_sem.osm_width_m > 0) else 3.0
                out_lane_width = (out_sem.osm_width_m / out_total_lanes) if (out_sem and out_sem.osm_width_m > 0) else 3.0
                
                inc_innermost_abs = inc_start_idx + 1
                out_innermost_abs = out_start_idx + 1
                
                # Lateral offsets (t-coordinate) for the reference line anchoring point:
                # OpenDRIVE defines positive t to the LEFT of the reference line, negative to the RIGHT.
                if is_lht and not viewer_safe:
                    # LHT forward lanes are on the left (+)
                    inc_sign = 1 if inc_dir == -1 else -1
                    out_sign = 1 if out_dir == -1 else -1
                else:
                    # RHT forward lanes are on the right (-)
                    inc_sign = -1 if inc_dir == -1 else 1
                    out_sign = -1 if out_dir == -1 else 1
                    
                inc_offset = (inc_innermost_abs - 1) * inc_lane_width * inc_sign
                out_offset = (out_innermost_abs - 1) * out_lane_width * out_sign

                junction.connections.append(
                    JunctionConnection(
                        incoming_road=inc_id,
                        connecting_road=out_id,
                        contact_point="start", 
                        lane_links=lane_links,
                        turn_angle_deg=angle,
                        inc_lateral_offset_m=inc_offset,
                        out_lateral_offset_m=out_offset,
                    )
                )
