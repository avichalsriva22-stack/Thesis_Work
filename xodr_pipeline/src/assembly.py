from scenariogeneration import xodr
from scenariogeneration.xodr import Orientation, ElementType, ContactPoint, DirectJunctionCreator
from typing import Dict
import xml.etree.ElementTree as ET
import traceback
from .models import RoadNode, LinePrimitive, ArcPrimitive, SpiralPrimitive, LaneSemantics
from .topology import RoadGraph




def _build_planview_xml(primitives: list) -> ET.Element:
    """
    Build a <planView> XML element directly from our LinePrimitive / ArcPrimitive list.

    scenariogeneration's PlanView.add_geometry() only stores the length of each
    segment — the absolute s, x, y, hdg values are only calculated lazily when
    adjust_roads_and_lanes() is called. Because we removed that call (it would
    overwrite our explicit UTM coordinates), we bypass the library entirely and
    construct the <planView> XML ourselves from the data already stored in each
    primitive.
    """
    pv = ET.Element('planView')
    for prim in primitives:
        geom = ET.SubElement(pv, 'geometry')
        geom.set('s',      f"{prim.s:.8f}")
        geom.set('x',      f"{prim.x:.8f}")
        geom.set('y',      f"{prim.y:.8f}")
        geom.set('hdg',    f"{prim.heading:.8f}")
        geom.set('length', f"{prim.length:.8f}")
        if isinstance(prim, LinePrimitive):
            ET.SubElement(geom, 'line')
        elif isinstance(prim, ArcPrimitive):
            arc_el = ET.SubElement(geom, 'arc')
            arc_el.set('curvature', f"{prim.curvature:.8f}")
        elif isinstance(prim, SpiralPrimitive):
            sp = ET.SubElement(geom, 'spiral')
            sp.set('curvStart', f"{prim.curvature_start:.8f}")
            sp.set('curvEnd', f"{prim.curvature_end:.8f}")
    return pv


def generate_xodr(graph: RoadGraph, country_code: str = "US") -> str:
    """
    Converts our RoadGraph into a scenariogeneration OpenDrive object,
    and returns the serialized XML string.
    """
    print(f"\n--- [Assembly] Assembling OpenDRIVE Object Model ---")
    print(f"[Assembly] Processing {len(graph.roads)} roads and {len(graph.junctions)} junctions...")
    
    odr = xodr.OpenDrive('Overture_OSM_Generated', revMajor='1', revMinor='6')
    
    xodr_roads: Dict[str, xodr.Road] = {}
    # Map numeric road ID -> list of primitives, for planView XML injection
    road_primitives: Dict[int, list] = {}

    # 1. Create Roads
    for road_id, road_node in graph.roads.items():
        try:
            numeric_id = abs(hash(road_id)) % (10 ** 8)

            # Collect geometry primitives (ExactPolylineStrategy always populates these)
            primitives = road_node.geometry.primitives if road_node.geometry.primitives else []
            if primitives:
                road_primitives[numeric_id] = primitives

            # Build a placeholder PlanView so scenariogeneration is happy.
            # The actual geometry XML will be injected later in _build_planview_xml().
            if primitives:
                p0 = primitives[0]
                planview = xodr.PlanView(x_start=p0.x, y_start=p0.y, h_start=p0.heading)
                # Add a single dummy Line so the road length attribute gets set.
                planview.add_geometry(xodr.Line(road_node.geometry.length or 1.0))
            elif road_node.geometry.points and len(road_node.geometry.points) > 1:
                # Spline path (fallback, should rarely be hit with ExactPolylineStrategy)
                pts = road_node.geometry.points
                p0 = pts[0]
                planview = xodr.PlanView(x_start=p0.x, y_start=p0.y, h_start=p0.heading)
                planview.add_geometry(xodr.Line(road_node.geometry.length or 1.0))
            else:
                x_start = road_node.geometry.points[0].x if road_node.geometry.points else 0.0
                y_start = road_node.geometry.points[0].y if road_node.geometry.points else 0.0
                heading = road_node.geometry.points[0].heading if road_node.geometry.points else 0.0
                print(f"[Assembly] Warning: Road '{road_id}' has degenerate planview. Using dummy 1 m segment.")
                planview = xodr.PlanView(x_start=x_start, y_start=y_start, h_start=heading)
                planview.add_geometry(xodr.Line(1.0))


            # ── Lane Width Resolution (3-priority cascade) ────────────────────────
            overture_total_width = 0.0
            if road_node.lane_widths:
                for rule in road_node.lane_widths:
                    if isinstance(rule, dict):
                        val = rule.get("value") or rule.get("width")
                        unit = str(rule.get("unit", "meters")).lower()
                        if val is not None:
                            try:
                                w = float(val)
                                if "foot" in unit or "feet" in unit or "ft" in unit:
                                    w *= 0.3048
                                if 3.0 <= w <= 50.0:
                                    overture_total_width = w
                            except (ValueError, TypeError):
                                pass
                        break  # First rule only

            sem = road_node.lane_semantics
            n_fwd = sem.n_forward  if sem else 1
            n_bwd = sem.n_backward if sem else 0
            total_lanes = max(1, n_fwd + n_bwd)

            osm_total_width = sem.osm_width_m if sem else 0.0
            standard_lane_w = 3.5  # fallback per lane
            standard_total  = standard_lane_w * total_lanes

            if overture_total_width >= 3.0:
                total_width = overture_total_width
            elif osm_total_width >= 3.0:
                total_width = osm_total_width
            else:
                total_width = standard_total

            lane_width = max(2.5, min(5.0, total_width / total_lanes))

            # ── Road Marks ────────────────────────────────────────────────────────
            rm_solid      = xodr.RoadMark(xodr.RoadMarkType.solid, 0.2)
            rm_broken     = xodr.RoadMark(xodr.RoadMarkType.broken, 0.15)
            rm_solid_solid= xodr.RoadMark(xodr.RoadMarkType.solid_solid, 0.2)

            center_lane = xodr.Lane(lane_type=xodr.LaneType.none)
            center_lane.add_roadmark(rm_solid_solid if n_bwd > 0 else rm_solid)

            lane_section = xodr.LaneSection(0.0, center_lane)

            # Right lanes (forward direction of travel)
            for i in range(n_fwd):
                outer = (i == n_fwd - 1)
                lane  = xodr.Lane(a=lane_width)
                lane.add_roadmark(rm_solid if outer else rm_broken)
                lane_section.add_right_lane(lane)

            # Left lanes (opposing / backward direction)
            for i in range(n_bwd):
                outer = (i == n_bwd - 1)
                lane  = xodr.Lane(a=lane_width)
                lane.add_roadmark(rm_solid if outer else rm_broken)
                lane_section.add_left_lane(lane)

            lanes = xodr.Lanes()
            lanes.add_lanesection(lane_section)

            # Create Road
            road = xodr.Road(numeric_id, planview, lanes, name=f"Road_{road_id[:6]}")
            
            if road_id.startswith("junc_"):
                parts = road_id.split("_")
                if len(parts) >= 4:
                    j_id = parts[1]
                    road.junction = abs(hash(j_id)) % (10 ** 8)
            
            # 2. Add Signals (Phase 3)
            for sig in road_node.signals:
                if sig.orientation == "+":
                    orient_val = Orientation.positive
                elif sig.orientation == "-":
                    orient_val = Orientation.negative
                else:
                    orient_val = Orientation.none

                signal_obj = xodr.Signal(
                    id=abs(hash(sig.id)) % (10 ** 8),
                    name=sig.type,
                    s=sig.s,
                    t=sig.t,
                    country=country_code,
                    orientation=orient_val,
                    Type=sig.type,
                    subtype="-1"
                )
                road.add_signal(signal_obj)
                
            odr.add_road(road)
            xodr_roads[road_id] = road
        except Exception as e:
            print(f"[Assembly] ERROR: Failed to assemble road '{road_id}': {e}")
            traceback.print_exc()

    # 3. Build Topology Links (First Pass: Wire all predecessor/successor links)
    for road_id, road_node in graph.roads.items():
        if road_id not in xodr_roads:
            continue
            
        xodr_road = xodr_roads[road_id]
        
        try:
            if road_node.predecessor:
                link = road_node.predecessor
                contact = ContactPoint.start if link.contact_point == "start" else ContactPoint.end
                elem_id = abs(hash(link.element_id)) % (10 ** 8)
                elem_type = ElementType.junction if link.element_type == "junction" else ElementType.road
                if elem_type == ElementType.junction:
                    xodr_road.add_predecessor(elem_type, elem_id)
                else:
                    xodr_road.add_predecessor(elem_type, elem_id, contact)
                
            if road_node.successor:
                link = road_node.successor
                contact = ContactPoint.start if link.contact_point == "start" else ContactPoint.end
                elem_id = abs(hash(link.element_id)) % (10 ** 8)
                elem_type = ElementType.junction if link.element_type == "junction" else ElementType.road
                if elem_type == ElementType.junction:
                    xodr_road.add_successor(elem_type, elem_id)
                else:
                    xodr_road.add_successor(elem_type, elem_id, contact)
        except Exception as e:
            print(f"[Assembly] Warning: Failed to wire initial links for road '{road_id}': {e}")

    valid_junctions = set()
    # 4. Build Junctions — common Junctions with physical connecting roads
    for j_id, j_node in graph.junctions.items():
        try:
            numeric_jid = abs(hash(j_id)) % (10 ** 8)
            junction = xodr.Junction(name=f"Junc_{j_id[:6]}", id=numeric_jid)
            conn_count = 0

            for conn in j_node.connections:
                if not conn.physical_road_id:
                    continue
                inc_road = xodr_roads.get(conn.incoming_road)
                phys_road = xodr_roads.get(conn.physical_road_id)
                if inc_road and phys_road:
                    try:
                        jconn = xodr.Connection(
                            incoming_road=inc_road.id,
                            connecting_road=phys_road.id,
                            contact_point=xodr.ContactPoint.start,
                        )
                        # The connecting road only has right lanes (-1, -2, etc)
                        # So we must map the incoming lanes to those sequentially
                        for i, ll in enumerate(conn.lane_links):
                            jconn.add_lanelink(ll.from_lane, -(i + 1))
                        junction.add_connection(jconn)
                        
                        # Mark the physical road as belonging to this junction
                        phys_road.junction = str(numeric_jid)
                        
                        conn_count += 1
                    except Exception as e:
                        print(
                            f"[Assembly] Warning: Junction link "
                            f"'{conn.incoming_road}'->'{conn.physical_road_id}' "
                            f"in junction '{j_id}': {e}"
                        )
            
            if conn_count > 0:
                odr.add_junction(junction)
                valid_junctions.add(j_id)
                print(
                    f"[Assembly] Junction '{j_id}' (ID: {numeric_jid}) "
                    f"assembled with {conn_count} connections."
                )
            else:
                print(f"[Assembly] Warning: Junction '{j_id}' — no physical roads to connect.")
        except Exception as e:
            print(f"[Assembly] ERROR: Failed to assemble junction '{j_id}': {e}")
            traceback.print_exc()

    # 5. Clean up dangling links pointing to skipped junctions
    for road_id, road_node in graph.roads.items():
        if road_id not in xodr_roads:
            continue
            
        xodr_road = xodr_roads[road_id]
        
        try:
            # Check predecessor
            if road_node.predecessor and road_node.predecessor.element_type == "junction":
                if road_node.predecessor.element_id not in valid_junctions:
                    print(f"[Assembly] Cleaning up skipped predecessor junction link '{road_node.predecessor.element_id}' from road '{road_id}'")
                    xodr_road.predecessor = None
                    xodr_road.links.links = [l for l in xodr_road.links.links if l.link_type != "predecessor"]
                    
            # Check successor
            if road_node.successor and road_node.successor.element_type == "junction":
                if road_node.successor.element_id not in valid_junctions:
                    print(f"[Assembly] Cleaning up skipped successor junction link '{road_node.successor.element_id}' from road '{road_id}'")
                    xodr_road.successor = None
                    xodr_road.links.links = [l for l in xodr_road.links.links if l.link_type != "successor"]
        except Exception as e:
            print(f"[Assembly] Warning: Failed to clean up links for road '{road_id}': {e}")
        
    # ── Serialize ────────────────────────────────────────────────────────────
    # NOTE: We intentionally do NOT call odr.adjust_roads_and_lanes() because
    # that function ignores our explicit UTM x_start/y_start values and
    # recalculates all positions from the road network topology (starting from
    # (0,0)), which scrambles the map layout.
    #
    # Instead, we post-process the serialized XML to replace every empty
    # <planView /> with the correctly built element from _build_planview_xml().
    try:
        raw_element = odr.get_element()
        xml_bytes = ET.tostring(raw_element, encoding='utf-8', xml_declaration=True)
        xml_string = xml_bytes.decode('utf-8')

        # ── Inject correct planView elements ──────────────────────────────
        print(f"[Assembly] Post-processing planView geometry injection for {len(road_primitives)} roads...")
        tree = ET.ElementTree(ET.fromstring(xml_string))
        root = tree.getroot()
        injected = skipped = 0
        for road_el in root.findall('road'):
            road_num_id = int(road_el.get('id', '-1'))
            if road_num_id not in road_primitives:
                skipped += 1
                continue
            prims = road_primitives[road_num_id]
            # Build correct planView element
            new_pv = _build_planview_xml(prims)
            # Find and replace existing planView child
            old_pv = road_el.find('planView')
            if old_pv is not None:
                idx = list(road_el).index(old_pv)
                road_el.remove(old_pv)
                road_el.insert(idx, new_pv)
            else:
                road_el.append(new_pv)
            injected += 1
        print(f"[Assembly] planView injection complete: {injected} injected, {skipped} skipped (no primitives).")

        connecting_road_ids = set()
        print(f"[Assembly] Post-processing laneLinks for connecting roads...")
        for j_id, j_node in graph.junctions.items():
            numeric_jid = abs(hash(j_id)) % (10 ** 8)
            for conn in j_node.connections:
                if not conn.physical_road_id:
                    continue
                connecting_road_ids.add(conn.physical_road_id)
                numeric_conn_id = abs(hash(conn.physical_road_id)) % (10 ** 8)
                conn_road_el = root.find(f'.//road[@id="{numeric_conn_id}"]')
                if conn_road_el is not None:
                    # Fix the junction attribute on the connecting road
                    conn_road_el.set('junction', str(numeric_jid))
                    
                    # We map from connecting road lanes (-(i+1)) to outgoing road lanes (ll.to_lane)
                    for i, ll in enumerate(conn.lane_links):
                        l_id_str = str(-(i + 1))
                        lane_el = conn_road_el.find(f'.//lane[@id="{l_id_str}"]')
                        if lane_el is not None:
                            link_el = lane_el.find('link')
                            if link_el is None:
                                link_el = ET.SubElement(lane_el, 'link')
                                
                            succ_el = link_el.find('successor')
                            if succ_el is None:
                                ET.SubElement(link_el, 'successor', {'id': str(ll.to_lane)})
                            else:
                                succ_el.set('id', str(ll.to_lane))
                                
                            pred_el = link_el.find('predecessor')
                            if pred_el is None:
                                ET.SubElement(link_el, 'predecessor', {'id': str(ll.from_lane)})
                            else:
                                pred_el.set('id', str(ll.from_lane))

        print(f"[Assembly] Post-processing laneLinks for direct road connections...")
        for road_id, road_node in graph.roads.items():
            # Skip connecting roads (they belong to a junction and are handled above)
            if road_id in connecting_road_ids:
                continue
                
            numeric_id = abs(hash(road_id)) % (10 ** 8)
            road_el = root.find(f'.//road[@id="{numeric_id}"]')
            if road_el is not None:
                sem = road_node.lane_semantics
                lanes = []
                if sem:
                    for i in range(1, sem.n_forward + 1):
                        lanes.append(-i)
                    for i in range(1, sem.n_backward + 1):
                        lanes.append(i)
                else:
                    lanes = [-1]

                for l_id in lanes:
                    l_id_str = str(l_id)
                    lane_el = road_el.find(f'.//lane[@id="{l_id_str}"]')
                    if lane_el is not None:
                        link_el = lane_el.find('link')
                        if link_el is None:
                            link_el = ET.SubElement(lane_el, 'link')
                            
                        if road_node.predecessor and road_node.predecessor.element_type == "road":
                            pred_el = link_el.find('predecessor')
                            if pred_el is None:
                                ET.SubElement(link_el, 'predecessor', {'id': l_id_str})
                            else:
                                pred_el.set('id', l_id_str)
                                
                        if road_node.successor and road_node.successor.element_type == "road":
                            succ_el = link_el.find('successor')
                            if succ_el is None:
                                ET.SubElement(link_el, 'successor', {'id': l_id_str})
                            else:
                                succ_el.set('id', l_id_str)

        # Re-serialize with injected planViews and laneLinks
        xml_bytes = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        xml_string = xml_bytes.decode('utf-8')
        print(f"[Assembly] Successfully serialized OpenDRIVE XML string. Size: {len(xml_string)} bytes.")
        return xml_string
    except Exception as e:
        print(f"[Assembly] ERROR: Failed to serialize OpenDRIVE XML: {e}")
        traceback.print_exc()
        raise RuntimeError(f"XML Serialization failed: {e}")

