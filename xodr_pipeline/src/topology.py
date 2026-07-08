from typing import Dict, List, Optional
from .models import (
    AnchoredOvertureSegment,
    AnchoredOvertureConnector,
    RoadNode,
    JunctionNode,
    RoadLink,
    JunctionConnection,
    PlanViewGeometry
)
from .geometry import segment_to_planview

class RoadGraph:
    def __init__(self):
        self.roads: Dict[str, RoadNode] = {}
        self.junctions: Dict[str, JunctionNode] = {}

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
            
            incoming_segs = []
            outgoing_segs = []
            
            for c_seg in connected_segs:
                seg_id = c_seg["segment_id"]
                is_incoming = c_seg["at"] > 0.5
                contact = "end" if is_incoming else "start"
                
                if is_incoming:
                    incoming_segs.append(seg_id)
                else:
                    outgoing_segs.append(seg_id)
                
                # Update the road to point to the junction
                link = RoadLink(element_type="junction", element_id=conn_id, contact_point=contact)
                if is_incoming:
                    graph.roads[seg_id].successor = link
                else:
                    graph.roads[seg_id].predecessor = link

            # Register all valid permutations of (incoming -> outgoing) connections
            for inc_id in incoming_segs:
                for out_id in outgoing_segs:
                    # In a full implementation, we would filter by turn angle rules
                    junction.connections.append(
                        JunctionConnection(
                            incoming_road=inc_id,
                            connecting_road=out_id,
                            contact_point="start"  # Outgoing road always contacts at its start
                        )
                    )

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
