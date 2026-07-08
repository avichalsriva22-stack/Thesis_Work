import pytest
from shapely.geometry import Point, LineString
from src.parsers import parse_turn_lanes, parse_traffic_signals
from src.enrichment import project_to_frenet

def test_turn_lane_parser():
    # Test simple case
    assert parse_turn_lanes("left|through|right") == ["left", "through", "right"]
    
    # Test empty string
    assert parse_turn_lanes("") == []
    
    # Test messy case with semicolons and none
    messy_tag = "left;through|none|right "
    result = parse_turn_lanes(messy_tag)
    assert result == ["left;through", "none", "right"]

def test_traffic_signal_parser():
    assert parse_traffic_signals({"highway": "traffic_signals"}) == "traffic_light"
    assert parse_traffic_signals({"highway": "stop"}) == "stop_sign"
    assert parse_traffic_signals({"amenity": "cafe"}) is None

def test_frenet_projection():
    # Create a straight centerline from (0,0) to (10,0)
    centerline = LineString([(0, 0), (10, 0)])
    
    # Point exactly halfway, but 3 meters to the left (y=3)
    # Since the line goes from x=0 to x=10, "left" means positive y.
    point_left = Point(5, 3)
    s_left, t_left = project_to_frenet(point_left, centerline)
    
    assert abs(s_left - 5.0) < 0.01
    assert abs(t_left - 3.0) < 0.01 # t should be positive for left
    
    # Point 2 meters to the right (y=-2)
    point_right = Point(7, -2)
    s_right, t_right = project_to_frenet(point_right, centerline)
    
    assert abs(s_right - 7.0) < 0.01
    assert abs(t_right - (-2.0)) < 0.01 # t should be negative for right
