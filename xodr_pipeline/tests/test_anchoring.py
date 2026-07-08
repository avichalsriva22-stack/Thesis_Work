import pytest
from shapely.geometry import Point, LineString
from src.anchoring import get_utm_zone_epsg, CoordinateAnchor

def test_utm_zone_calculation():
    # Test a few known coordinates
    # New York City: roughly -74, 40 (Zone 18N -> EPSG 32618)
    assert get_utm_zone_epsg(-74.0, 40.0) == 32618
    
    # Sydney: roughly 151, -33 (Zone 56S -> EPSG 32756)
    assert get_utm_zone_epsg(151.0, -33.0) == 32756
    
    # London: roughly 0, 51 (Zone 30N -> EPSG 32630 or 31N)
    assert get_utm_zone_epsg(-0.1, 51.5) == 32630

def test_coordinate_anchor_projection():
    target_epsg = 32618 # UTM Zone 18N
    anchor = CoordinateAnchor(target_epsg)
    
    # Project a point in WGS84
    pt = anchor.project_point(-74.0, 40.0)
    
    # Check that it's no longer in WGS84 bounds (-180/180, -90/90)
    assert pt.x > 180 or pt.x < -180
    assert pt.y > 90 or pt.y < -90
    
def test_osm_scaling():
    # Test the specific 1e7 scaling behavior
    anchor = CoordinateAnchor(32618)
    
    raw_osm_data = {
        "nodes": [
            {
                "id": 1,
                "lon": -740000000, # -74.0 scaled by 1e7
                "lat": 400000000,  # 40.0 scaled by 1e7
                "tags": {}
            }
        ]
    }
    
    result = anchor.process_osm_data(raw_osm_data, is_scaled_1e7=True)
    assert 1 in result['nodes']
    pt = result['nodes'][1].geometry
    
    # Verify the coordinates are in metric space (meaning they were properly scaled back down before projection)
    assert pt.x > 180 or pt.x < -180
    assert pt.y > 90 or pt.y < -90

def test_overture_projection():
    anchor = CoordinateAnchor(32618)
    
    raw_overture_data = {
        "connectors": [
            {
                "id": "conn1",
                "geometry": {"type": "Point", "coordinates": [-74.0, 40.0]}
            }
        ],
        "segments": [
            {
                "id": "seg1",
                "geometry": {"type": "LineString", "coordinates": [[-74.0, 40.0], [-74.1, 40.1]]},
                "connectors": [{"connector_id": "conn1", "at": 0.0}]
            }
        ]
    }
    
    result = anchor.process_overture_data(raw_overture_data)
    
    assert "conn1" in result["connectors"]
    assert "seg1" in result["segments"]
    
    seg = result["segments"]["seg1"]
    assert isinstance(seg.geometry, LineString)
    # the geometry should be in UTM coordinates
    assert seg.geometry.coords[0][0] > 180
