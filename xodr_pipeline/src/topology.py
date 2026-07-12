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
        return (prims[-1].heading + math.pi) % (2 * math.pi)

def _endpoint_xy(road: "RoadNode", contact: str) -> tuple[float, float]:
    prims = road.geometry.primitives
    if contact == "start":
        p = prims[0]
        return (p.x, p.y)
    else:
        p = prims[-1]
        end_x = p.x + p.length * math.cos(p.heading)
        end_y = p.y + p.length * math.sin(p.heading)
        return (end_x, end_y)


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
        return prims[-1].heading % (2 * math.pi)

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

def resolve_junction_lane_connections(graph: "RoadGraph") -> None:
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
                
                # Create lane links. OpenDRIVE lanes: negative = right/forward, positive = left/backward
                # For connecting roads, they are one-way so their lanes are -1, -2...
                # We map incoming lanes (either negative or positive) to the connecting road (-1, -2)
                # and then from the connecting road (-1, -2) to the outgoing lanes (negative or positive)
                # In junction.connections, we just track the final from->to, junction_geometry handles the middleman.
                
                # If incoming is forward (-1), we map -1 to connecting -1.
                # If incoming is backward (+1), we map +1 to connecting -1.
                # If outgoing is forward (-1), we map connecting -1 to -1.
                # If outgoing is backward (+1), we map connecting -1 to +1.
                
                # Wait, LaneLink currently assumes from_lane is on inc_road, and to_lane is on out_road.
                lane_links = []
                for i in range(n_lanes):
                    from_l = -(i + 1) if inc_dir == -1 else (i + 1)
                    to_l = -(i + 1) if out_dir == -1 else (i + 1)
                    lane_links.append(LaneLink(from_lane=from_l, to_lane=to_l))

                # Since a connecting road's reference line goes from incoming contact to outgoing contact
                # The contact points are determined by the direction.
                # If incoming direction is forward (-1), we connect from its "end".
                # If incoming direction is backward (1), we connect from its "start".
                # The contact point ON THE INCOMING ROAD where the connection starts.
                # In JunctionConnection, we historically stored `contact_point="start"` (meaning the connecting road starts at the incoming road).
                # Wait, OpenDRIVE connecting roads always start at the incoming road and end at the outgoing road.
                
                junction.connections.append(
                    JunctionConnection(
                        incoming_road=inc_id,
                        connecting_road=out_id,
                        contact_point="start", # Connecting road always connects its start to incoming
                        lane_links=lane_links,
                        turn_angle_deg=angle,
                    )
                )
