import pytest
from shapely.geometry import LineString
from src.geometry import SmoothedSplineStrategy, PrimitiveFittingStrategy
from src.models import AnchoredOvertureSegment

def test_smoothed_spline_strategy():
    ls = LineString([(0, 0), (5, 0), (10, 0)])
    seg = AnchoredOvertureSegment(id="test", geometry=ls, connectors=[])
    
    # Use window_length smaller than number of points for this simple test, or it won't filter
    strategy = SmoothedSplineStrategy(step=1.0, window_length=3, polyorder=1)
    planview = strategy.fit(seg)
    
    assert abs(planview.length - 10.0) < 0.1
    # Check that points were generated
    assert len(planview.points) >= 2
    
    for p in planview.points:
        assert abs(p.curvature) < 0.01

def test_primitive_fitting_strategy():
    # 1. Straight line -> should emit LinePrimitive
    ls_line = LineString([(0, 0), (5, 0), (10, 0)])
    seg_line = AnchoredOvertureSegment(id="test_line", geometry=ls_line, connectors=[])
    
    strat = PrimitiveFittingStrategy()
    planview_line = strat.fit(seg_line)
    
    assert len(planview_line.primitives) == 1
    assert type(planview_line.primitives[0]).__name__ == "LinePrimitive"
    
    # 2. Curve -> should emit ArcPrimitive
    # Quarter circle approximation
    ls_curve = LineString([(0, 0), (0.1, 0.5), (0.5, 0.9), (1, 1)])
    seg_curve = AnchoredOvertureSegment(id="test_curve", geometry=ls_curve, connectors=[])
    
    planview_curve = strat.fit(seg_curve)
    assert len(planview_curve.primitives) == 1
    assert type(planview_curve.primitives[0]).__name__ == "ArcPrimitive"
    # Curvature should be non-zero
    assert abs(planview_curve.primitives[0].curvature) > 0.1
