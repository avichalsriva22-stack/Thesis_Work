import pyclothoids as pcloth
import math
from typing import Dict, List
from .models import RoadNode, SpiralPrimitive, PlanViewGeometry, RoadLink, LinePrimitive, ArcPrimitive, LaneSemantics
from .topology import RoadGraph, _endpoint_xy, _heading_into_junction

def _trim_primitives_end(prims: List, trim_dist: float):
    remaining = float(trim_dist)
    while prims and remaining > 0.001:
        last = prims[-1]
        if last.length <= remaining:
            remaining -= last.length
            prims.pop()
        else:
            last.length -= remaining
            remaining = 0.0

def _trim_primitives_start(prims: List, trim_dist: float):
    remaining = float(trim_dist)
    while prims and remaining > 0.001:
        first = prims[0]
        if first.length <= remaining:
            remaining -= first.length
            prims.pop(0)
        else:
            first.length -= remaining
            if isinstance(first, LinePrimitive):
                first.x += remaining * math.cos(first.heading)
                first.y += remaining * math.sin(first.heading)
            elif isinstance(first, ArcPrimitive):
                first.x += remaining * math.cos(first.heading + remaining * first.curvature / 2.0)
                first.heading += remaining * first.curvature
            remaining = 0.0
            
    # Shift s values
    current_s = 0.0
    for p in prims:
        p.s = current_s
        current_s += p.length

def build_junction_connecting_roads(graph: "RoadGraph") -> Dict[str, RoadNode]:
    """
    Must run after resolve_junction_lane_connections() has populated
    junction.connections. For every JunctionConnection, generates a real
    physical connecting RoadNode with G2-continuous clothoid geometry
    bridging the incoming road's endpoint to the outgoing road's endpoint,
    in the SAME absolute coordinate frame as the rest of the network
    (no adjust_roads_and_lanes() needed).
    """
    connecting_roads: Dict[str, RoadNode] = {}
    
    # 1. Truncate roads so they don't overlap at the exact node center
    TRIM_DISTANCE = 10.0 # meters
    truncated = set()
    
    for junction in graph.junctions.values():
        for conn in junction.connections:
            inc_road = graph.roads[conn.incoming_road]
            out_road = graph.roads[conn.connecting_road]

            # Find the contact point where these roads touch THIS junction
            inc_contact = next(c for sid, c in junction.connected_roads if sid == conn.incoming_road)
            out_contact = next(c for sid, c in junction.connected_roads if sid == conn.connecting_road)
            
            # Calculate safe trim distances (max 49% of road length to avoid emptying it)
            inc_trim = min(TRIM_DISTANCE, inc_road.geometry.length * 0.49)
            out_trim = min(TRIM_DISTANCE, out_road.geometry.length * 0.49)
            
            # Truncate incoming road ONCE
            inc_key = f"{inc_road.id}_{inc_contact}"
            if inc_key not in truncated:
                if inc_contact == "end":
                    _trim_primitives_end(inc_road.geometry.primitives, inc_trim)
                else:
                    _trim_primitives_start(inc_road.geometry.primitives, inc_trim)
                inc_road.geometry.length = sum(p.length for p in inc_road.geometry.primitives)
                truncated.add(inc_key)
                
            # Truncate outgoing road ONCE
            out_key = f"{out_road.id}_{out_contact}"
            if out_key not in truncated:
                if out_contact == "end":
                    _trim_primitives_end(out_road.geometry.primitives, out_trim)
                else:
                    _trim_primitives_start(out_road.geometry.primitives, out_trim)
                out_road.geometry.length = sum(p.length for p in out_road.geometry.primitives)
                truncated.add(out_key)

    # 2. Build the smooth clothoid geometry bridging the new gap
    for junction in graph.junctions.values():
        for conn in junction.connections:
            inc_road = graph.roads[conn.incoming_road]
            out_road = graph.roads[conn.connecting_road]

            # Find the contact point where these roads touch THIS junction
            inc_contact = next(c for sid, c in junction.connected_roads if sid == conn.incoming_road)
            out_contact = next(c for sid, c in junction.connected_roads if sid == conn.connecting_road)

            x0, y0 = _endpoint_xy(inc_road, inc_contact)
            h0 = _heading_into_junction(inc_road, inc_contact)
            x1, y1 = _endpoint_xy(out_road, out_contact)
            h1 = (_heading_into_junction(out_road, out_contact) + math.pi) % (2 * math.pi)

            # In some degenerate cases if trimming wasn't enough (or roads are tiny)
            if math.hypot(x1 - x0, y1 - y0) < 0.1:
                print(f"[Geometry] Warning: Connection gap too small for {conn.incoming_road}->{conn.connecting_road}")
                continue

            clothoids = pcloth.SolveG2(x0, y0, h0, 0.0, x1, y1, h1, 0.0)

            primitives = []
            s_cursor = 0.0
            for c in clothoids:
                prim = SpiralPrimitive(
                    s=s_cursor, x=c.XStart, y=c.YStart, heading=c.ThetaStart,
                    length=c.length,
                    curvature_start=c.KappaStart, curvature_end=c.KappaEnd,
                )
                primitives.append(prim)
                s_cursor += c.length

            total_length = s_cursor
            conn_road_id = f"junc_{junction.id}_{conn.incoming_road}_{conn.connecting_road}"

            road = RoadNode(
                id=conn_road_id,
                geometry=PlanViewGeometry(length=total_length, primitives=primitives),
                lane_semantics=LaneSemantics(
                    turn_rules=[],
                    n_forward=len(conn.lane_links),
                    n_backward=0,
                    is_oneway=True,
                    osm_width_m=inc_road.lane_semantics.osm_width_m if inc_road.lane_semantics else 0.0
                )
            )
            road.predecessor = RoadLink(element_type="road", element_id=conn.incoming_road, contact_point=inc_contact)
            road.successor = RoadLink(element_type="road", element_id=conn.connecting_road, contact_point=out_contact)

            connecting_roads[conn_road_id] = road
            conn.physical_road_id = conn_road_id

    return connecting_roads
