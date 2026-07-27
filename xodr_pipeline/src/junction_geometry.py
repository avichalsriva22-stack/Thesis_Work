import pyclothoids as pcloth
import math
from typing import Dict, List
from .models import RoadNode, SpiralPrimitive, PlanViewGeometry, RoadLink, LinePrimitive, ArcPrimitive, LaneSemantics
from .topology import RoadGraph, _endpoint_xy, _primitive_end_pose

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
                if abs(first.curvature) > 1e-6:
                    first.x += (math.sin(first.heading + remaining * first.curvature) - math.sin(first.heading)) / first.curvature
                    first.y -= (math.cos(first.heading + remaining * first.curvature) - math.cos(first.heading)) / first.curvature
                else:
                    first.x += remaining * math.cos(first.heading)
                    first.y += remaining * math.sin(first.heading)
                first.heading = (first.heading + remaining * first.curvature) % (2 * math.pi)
            remaining = 0.0
            
    # Shift s values
    current_s = 0.0
    for p in prims:
        p.s = current_s
        current_s += p.length


def _get_heading_after_trim(road: "RoadNode", contact: str) -> float:
    """
    Get the heading at the road's trimmed endpoint.
    This reads directly from the (already-mutated) primitive list,
    ensuring headings are correct AFTER trimming.
    
    Returns the heading of TRAVEL at the endpoint:
    - contact=="end": heading of the last primitive at its end
    - contact=="start": heading of the first primitive at its start
    """
    prims = road.geometry.primitives
    if not prims:
        return 0.0
    if contact == "end":
        # Heading at the END of the last primitive = direction of travel arriving
        _, _, hdg = _primitive_end_pose(prims[-1])
        return hdg % (2 * math.pi)
    else:
        # Heading at the START of the first primitive = direction of travel leaving
        return prims[0].heading % (2 * math.pi)


def _make_line_primitive(x0, y0, x1, y1) -> LinePrimitive:
    """Create a simple straight line primitive between two points."""
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    heading = math.atan2(dy, dx)
    return LinePrimitive(s=0.0, x=x0, y=y0, heading=heading, length=length)


def _make_arc_primitive(x0, y0, h0, x1, y1) -> ArcPrimitive:
    """Create a constant-curvature arc from (x0,y0,h0) toward (x1,y1)."""
    dx = x1 - x0
    dy = y1 - y0
    chord = math.hypot(dx, dy)
    if chord < 0.01:
        return ArcPrimitive(s=0.0, x=x0, y=y0, heading=h0, length=0.01, curvature=0.0)
    
    # Direction from start to end
    phi = math.atan2(dy, dx)
    # Deflection angle between heading and chord
    delta = phi - h0
    # Normalize to [-pi, pi]
    delta = (delta + math.pi) % (2 * math.pi) - math.pi
    
    if abs(delta) < 1e-4:
        # Nearly straight
        return ArcPrimitive(s=0.0, x=x0, y=y0, heading=h0, length=chord, curvature=0.0)
    
    # For a circular arc: curvature = 2*sin(delta)/chord
    curvature = 2.0 * math.sin(delta) / chord
    # Arc length = 2 * delta / curvature (but safer to compute from angle)
    if abs(curvature) > 1e-6:
        arc_length = abs(2.0 * delta / curvature)
    else:
        arc_length = chord
    
    return ArcPrimitive(s=0.0, x=x0, y=y0, heading=h0, length=arc_length, curvature=curvature)


def build_junction_connecting_roads(graph: "RoadGraph") -> Dict[str, RoadNode]:
    """
    Must run after resolve_junction_lane_connections() has populated
    junction.connections. For every JunctionConnection, generates a real
    physical connecting RoadNode with smooth geometry bridging the
    incoming road's endpoint to the outgoing road's endpoint.
    
    Uses pyclothoids.SolveG2 for smooth G2-continuous curves, with
    a fallback to simple arcs if the clothoid solution is degenerate.
    """
    connecting_roads: Dict[str, RoadNode] = {}
    
    # 1. Truncate roads so they don't overlap at the exact node center
    # Use a proportional trim distance instead of a fixed 10m
    MAX_TRIM = 8.0  # max trim in metres
    MIN_TRIM = 2.0  # min trim in metres
    truncated = set()
    
    for junction in graph.junctions.values():
        for conn in junction.connections:
            inc_road = graph.roads[conn.incoming_road]
            out_road = graph.roads[conn.connecting_road]

            # Find the contact point where these roads touch THIS junction
            inc_contact = next(c for sid, c in junction.connected_roads if sid == conn.incoming_road)
            out_contact = next(c for sid, c in junction.connected_roads if sid == conn.connecting_road)
            
            # Proportional trim: 30% of road length
            # BUT ensure it's at least 2m larger than the lateral offset to prevent 
            # start/end points from crossing each other on tight turns
            min_inc_trim = max(MIN_TRIM, abs(conn.inc_lateral_offset_m) + 2.0)
            min_out_trim = max(MIN_TRIM, abs(conn.out_lateral_offset_m) + 2.0)
            
            inc_trim = max(min_inc_trim, min(MAX_TRIM, inc_road.geometry.length * 0.30))
            out_trim = max(min_out_trim, min(MAX_TRIM, out_road.geometry.length * 0.30))
            
            # Never trim more than 49% to avoid emptying the road
            inc_trim = min(inc_trim, inc_road.geometry.length * 0.49)
            out_trim = min(out_trim, out_road.geometry.length * 0.49)
            
            # Truncate incoming road ONCE per contact
            inc_key = f"{inc_road.id}_{inc_contact}"
            if inc_key not in truncated:
                if inc_contact == "end":
                    _trim_primitives_end(inc_road.geometry.primitives, inc_trim)
                else:
                    _trim_primitives_start(inc_road.geometry.primitives, inc_trim)
                inc_road.geometry.length = sum(p.length for p in inc_road.geometry.primitives)
                truncated.add(inc_key)
                
            # Truncate outgoing road ONCE per contact
            out_key = f"{out_road.id}_{out_contact}"
            if out_key not in truncated:
                if out_contact == "end":
                    _trim_primitives_end(out_road.geometry.primitives, out_trim)
                else:
                    _trim_primitives_start(out_road.geometry.primitives, out_trim)
                out_road.geometry.length = sum(p.length for p in out_road.geometry.primitives)
                truncated.add(out_key)

    # 2. Build geometry bridging the new gap — headings read AFTER trimming
    for junction in graph.junctions.values():
        for conn in junction.connections:
            inc_road = graph.roads[conn.incoming_road]
            out_road = graph.roads[conn.connecting_road]

            # Find the contact points
            inc_contact = next(c for sid, c in junction.connected_roads if sid == conn.incoming_road)
            out_contact = next(c for sid, c in junction.connected_roads if sid == conn.connecting_road)

            # Get positions at the trimmed endpoints
            x0, y0 = _endpoint_xy(inc_road, inc_contact, conn.inc_lateral_offset_m)
            x1, y1 = _endpoint_xy(out_road, out_contact, conn.out_lateral_offset_m)

            # Get headings AFTER trimming — this is the critical fix.
            if inc_contact == "end":
                h0 = _get_heading_after_trim(inc_road, "end")
            else:
                # Traffic flowing backward arrives at start
                h0 = (_get_heading_after_trim(inc_road, "start") + math.pi) % (2 * math.pi)
            
            if out_contact == "start":
                h1 = _get_heading_after_trim(out_road, "start")
            else:
                # Traffic flowing backward leaves from end
                h1 = (_get_heading_after_trim(out_road, "end") + math.pi) % (2 * math.pi)

            dist = math.hypot(x1 - x0, y1 - y0)
            if dist < 0.1:
                print(f"[Junction] Warning: Gap too small ({dist:.3f}m) for "
                      f"{conn.incoming_road[:10]}→{conn.connecting_road[:10]}, skipping.")
                continue

            # Check for backwards crossing (degenerate topology due to short roads and large offsets)
            # If the points have crossed, NO curve can connect them without looping.
            # And a straight line would face backwards, causing mesh generation explosion (shooting lines) in viewers.
            vector_x = x1 - x0
            vector_y = y1 - y0
            dot0 = vector_x * math.cos(h0) + vector_y * math.sin(h0)
            dot1 = vector_x * math.cos(h1) + vector_y * math.sin(h1)
            
            if dot0 < -0.1 or dot1 < -0.1:
                print(f"[Junction] Warning: Degenerate topology (anchor points crossed backwards). "
                      f"Dropping connection {conn.incoming_road[:10]}->{conn.connecting_road[:10]} to prevent geometry explosion.")
                continue

            # Try clothoid G2 solution first
            primitives = []
            total_length = 0.0
            use_fallback = False
            
            try:
                clothoids = pcloth.SolveG2(x0, y0, h0, 0.0, x1, y1, h1, 0.0)
                
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
                
                # Sanity check: if the clothoid path is excessively long relative
                # to the straight-line distance, it means the solver produced a
                # looping/degenerate curve. Fall back to a simple arc.
                MAX_LENGTH_RATIO = 1.5
                if total_length > dist * MAX_LENGTH_RATIO:
                    print(f"[Junction] Warning: Clothoid path too long "
                          f"({total_length:.1f}m vs {dist:.1f}m gap) for "
                          f"{conn.incoming_road[:10]}→{conn.connecting_road[:10]}, "
                          f"using arc fallback.")
                    use_fallback = True
                    
            except Exception as e:
                print(f"[Junction] Clothoid solver failed for {conn.incoming_road[:10]}→{conn.connecting_road[:10]}: {e}")
                use_fallback = True
            
            if use_fallback:
                arc = _make_arc_primitive(x0, y0, h0, x1, y1)
                primitives = [arc]
                total_length = arc.length

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
