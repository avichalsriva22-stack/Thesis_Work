import pytest
from shapely.geometry import LineString, Point
from src.topology import build_topology
from src.models import AnchoredOvertureSegment, AnchoredOvertureConnector, SegmentConnectorRef

def test_simple_road_link():
    # Two segments connected at one connector
    connectors = {
        "conn1": AnchoredOvertureConnector(id="conn1", geometry=Point(5, 0))
    }
    
    segments = {
        "segA": AnchoredOvertureSegment(
            id="segA",
            geometry=LineString([(0, 0), (5, 0)]),
            connectors=[SegmentConnectorRef(id="conn1", at=1.0)] # Ends at conn1
        ),
        "segB": AnchoredOvertureSegment(
            id="segB",
            geometry=LineString([(5, 0), (10, 0)]),
            connectors=[SegmentConnectorRef(id="conn1", at=0.0)] # Starts at conn1
        )
    }
    
    graph = build_topology(segments, connectors)
    
    # Assert nodes exist
    assert "segA" in graph.roads
    assert "segB" in graph.roads
    
    # Assert A's successor is B
    assert graph.roads["segA"].successor is not None
    assert graph.roads["segA"].successor.element_id == "segB"
    assert graph.roads["segA"].successor.contact_point == "start"
    
    # Assert B's predecessor is A
    assert graph.roads["segB"].predecessor is not None
    assert graph.roads["segB"].predecessor.element_id == "segA"
    assert graph.roads["segB"].predecessor.contact_point == "end"
    
    # No complex junction should be created
    assert "conn1" not in graph.junctions

def test_intersection_topology():
    # Three segments meeting at one connector
    connectors = {
        "conn_center": AnchoredOvertureConnector(id="conn_center", geometry=Point(5, 5))
    }
    
    segments = {
        "seg_west": AnchoredOvertureSegment(
            id="seg_west",
            geometry=LineString([(0, 5), (5, 5)]),
            connectors=[SegmentConnectorRef(id="conn_center", at=1.0)]
        ),
        "seg_east": AnchoredOvertureSegment(
            id="seg_east",
            geometry=LineString([(5, 5), (10, 5)]),
            connectors=[SegmentConnectorRef(id="conn_center", at=0.0)]
        ),
        "seg_north": AnchoredOvertureSegment(
            id="seg_north",
            geometry=LineString([(5, 10), (5, 5)]),
            connectors=[SegmentConnectorRef(id="conn_center", at=1.0)]
        )
    }
    
    graph = build_topology(segments, connectors)
    
    # Assert junction was created
    assert "conn_center" in graph.junctions
    junction = graph.junctions["conn_center"]
    
    # Assert it has connections
    assert len(junction.connections) == 2
    
    # Assert seg_west points to the junction as its successor
    assert graph.roads["seg_west"].successor is not None
    assert graph.roads["seg_west"].successor.element_type == "junction"
    assert graph.roads["seg_west"].successor.element_id == "conn_center"
