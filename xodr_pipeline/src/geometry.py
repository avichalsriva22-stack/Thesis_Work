import numpy as np
from abc import ABC, abstractmethod
from scipy.interpolate import splprep, splev
from scipy.signal import savgol_filter
from typing import List
from shapely.geometry import LineString

from .models import (
    AnchoredOvertureSegment,
    PlanViewGeometry,
    PlanViewPoint,
    LinePrimitive,
    ArcPrimitive,
    SpiralPrimitive,
)


# ---------------------------------------------------------------------------
# Utility: filter consecutive near-duplicate coordinates
# ---------------------------------------------------------------------------
def _deduplicate_coords(coords: np.ndarray, min_dist: float = 0.05) -> np.ndarray:
    """Remove consecutive points that are closer than min_dist metres."""
    if len(coords) < 2:
        return coords
    keep = [coords[0]]
    for pt in coords[1:]:
        if np.linalg.norm(pt - keep[-1]) >= min_dist:
            keep.append(pt)
    result = np.array(keep)
    # Always keep the last original point to preserve exact endpoint
    if np.linalg.norm(result[-1] - coords[-1]) > 1e-6:
        result = np.vstack([result, coords[-1]])
    return result


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------
class GeometryStrategy(ABC):
    @abstractmethod
    def fit(self, segment: AnchoredOvertureSegment) -> PlanViewGeometry:
        pass


# ---------------------------------------------------------------------------
# ExactPolylineStrategy — THE DEFAULT
# Encodes every vertex of the Overture / OSM LineString as a LinePrimitive.
# This is geometrically exact: every bend and turn is preserved.
# ---------------------------------------------------------------------------
class ExactPolylineStrategy(GeometryStrategy):
    """
    Converts the raw coordinate polyline from an AnchoredOvertureSegment
    directly into a sequence of OpenDRIVE LinePrimitive objects.

    No spline fitting, no averaging — the road centerline exactly follows
    the source data (Overture Maps / OSM).
    """

    def fit(self, segment: AnchoredOvertureSegment) -> PlanViewGeometry:
        print(f"[Geometry] Encoding exact polyline for segment '{segment.id}'...")
        try:
            raw = np.array(segment.geometry.coords, dtype=float)
            coords = _deduplicate_coords(raw, min_dist=0.05)

            if len(coords) < 2:
                raise ValueError(
                    f"Segment has fewer than 2 distinct vertices after deduplication "
                    f"(raw={len(raw)}, filtered={len(coords)})"
                )

            primitives: List[LinePrimitive] = []
            points: List[PlanViewPoint] = []
            current_s = 0.0

            for i in range(len(coords) - 1):
                p0 = coords[i]
                p1 = coords[i + 1]
                dx = float(p1[0] - p0[0])
                dy = float(p1[1] - p0[1])
                seg_len = float(np.hypot(dx, dy))

                if seg_len < 1e-4:
                    # Skip degenerate micro-segments
                    continue

                heading = float(np.arctan2(dy, dx))

                primitives.append(LinePrimitive(
                    s=current_s,
                    x=float(p0[0]),
                    y=float(p0[1]),
                    heading=heading,
                    length=seg_len,
                ))
                points.append(PlanViewPoint(
                    s=current_s,
                    x=float(p0[0]),
                    y=float(p0[1]),
                    heading=heading,
                    curvature=0.0,
                ))
                current_s += seg_len

            if not primitives:
                raise ValueError("All sub-segments were degenerate (zero length).")

            # Add the final endpoint
            last_prim = primitives[-1]
            last_heading = last_prim.heading
            points.append(PlanViewPoint(
                s=current_s,
                x=float(coords[-1][0]),
                y=float(coords[-1][1]),
                heading=last_heading,
                curvature=0.0,
            ))

            print(
                f"[Geometry] Segment '{segment.id}': {len(primitives)} line primitives, "
                f"total length={current_s:.2f} m"
            )
            return PlanViewGeometry(length=current_s, points=points, primitives=primitives)

        except Exception as e:
            print(
                f"[Geometry] ERROR in ExactPolylineStrategy for segment '{segment.id}': {e}. "
                f"Using 1 m dummy segment."
            )
            coords = np.array(segment.geometry.coords, dtype=float)
            x0, y0 = (float(coords[0][0]), float(coords[0][1])) if len(coords) > 0 else (0.0, 0.0)
            dummy_prim = LinePrimitive(s=0.0, x=x0, y=y0, heading=0.0, length=1.0)
            p0 = PlanViewPoint(s=0.0, x=x0, y=y0, heading=0.0, curvature=0.0)
            p1 = PlanViewPoint(s=1.0, x=x0 + 1.0, y=y0, heading=0.0, curvature=0.0)
            return PlanViewGeometry(length=1.0, points=[p0, p1], primitives=[dummy_prim])


# ---------------------------------------------------------------------------
# SmoothedSplineStrategy — kept for reference / optional use
# ---------------------------------------------------------------------------
class SmoothedSplineStrategy(GeometryStrategy):
    """
    Applies Savitzky-Golay filtering then fits a B-spline.
    This strategy can produce smoother-looking roads but may deviate from
    the source data in places with tight turns or sparse vertices.
    """

    def __init__(self, step: float = 1.0, window_length: int = 5, polyorder: int = 2):
        self.step = step
        self.window_length = window_length
        self.polyorder = polyorder

    def fit(self, segment: AnchoredOvertureSegment) -> PlanViewGeometry:
        print(f"[Geometry] Fitting B-spline for segment '{segment.id}'...")
        try:
            raw = np.array(segment.geometry.coords, dtype=float)
            coords = _deduplicate_coords(raw, min_dist=0.05)
            num_points = len(coords)

            if num_points < 2:
                raise ValueError("Fewer than 2 distinct vertices after deduplication.")

            # Savitzky-Golay smoothing
            if num_points >= self.window_length:
                x_smooth = savgol_filter(coords[:, 0], self.window_length, self.polyorder)
                y_smooth = savgol_filter(coords[:, 1], self.window_length, self.polyorder)
            else:
                x_smooth = coords[:, 0]
                y_smooth = coords[:, 1]

            k = min(3, num_points - 1)
            if k < 1:
                raise ValueError("Need at least 2 points for spline.")

            tck, u = splprep([x_smooth, y_smooth], s=float(num_points) * 0.5, k=k)

            u_fine = np.linspace(0, 1, 1000)
            x_fine, y_fine = splev(u_fine, tck)
            dx = np.diff(x_fine)
            dy = np.diff(y_fine)
            distances = np.sqrt(dx ** 2 + dy ** 2)
            s_fine = np.concatenate(([0], np.cumsum(distances)))
            length = float(s_fine[-1])

            # Sanity check
            chord_len = float(np.linalg.norm(coords[-1] - coords[0]))
            if length > 5.0 * chord_len and chord_len > 1.0:
                raise ValueError(
                    f"Spline unstable: spline length {length:.1f} m >> chord {chord_len:.1f} m."
                )

            pts: List[PlanViewPoint] = []
            current_s = 0.0
            iters = 0
            while current_s <= length:
                iters += 1
                if iters > 5000:
                    raise ValueError("Spline evaluation exceeded 5000 iterations.")
                u_val = float(np.interp(current_s, s_fine, u_fine))
                x, y = splev(u_val, tck)
                dx_v, dy_v = splev(u_val, tck, der=1)
                heading = np.arctan2(dy_v, dx_v)
                if k >= 2:
                    d2x, d2y = splev(u_val, tck, der=2)
                    denom = (dx_v ** 2 + dy_v ** 2) ** 1.5
                    curvature = float((dx_v * d2y - dy_v * d2x) / denom) if denom > 1e-10 else 0.0
                else:
                    curvature = 0.0
                pts.append(PlanViewPoint(
                    s=current_s, x=float(x), y=float(y),
                    heading=float(heading), curvature=curvature
                ))
                abs_curv = abs(curvature)
                step = 10.0 if abs_curv < 0.0005 else (5.0 if abs_curv < 0.005 else 1.0)
                current_s += step

            # Ensure endpoint is captured
            u_val = 1.0
            x, y = splev(u_val, tck)
            dx_v, dy_v = splev(u_val, tck, der=1)
            heading = np.arctan2(dy_v, dx_v)
            pts.append(PlanViewPoint(
                s=length, x=float(x), y=float(y),
                heading=float(heading), curvature=0.0
            ))

            return PlanViewGeometry(length=length, points=pts)

        except Exception as e:
            # Fall through to ExactPolylineStrategy as the true fallback
            print(f"[Geometry] Spline failed for '{segment.id}' ({e}). Using ExactPolylineStrategy.")
            return ExactPolylineStrategy().fit(segment)


# ---------------------------------------------------------------------------
# PrimitiveFittingStrategy — kept for reference / optional use
# ---------------------------------------------------------------------------
class PrimitiveFittingStrategy(GeometryStrategy):
    """
    Segments the geometry into analytical Lines and Arcs based on curvature.
    Falls back to ExactPolylineStrategy on failure.
    """

    def fit(self, segment: AnchoredOvertureSegment) -> PlanViewGeometry:
        print(f"[Geometry] Fitting primitives (lines/arcs) for segment '{segment.id}'...")
        try:
            coords = np.array(segment.geometry.coords, dtype=float)
            num_points = len(coords)

            if num_points < 2:
                raise ValueError("Need at least 2 points.")

            diffs = np.diff(coords, axis=0)
            seg_lengths = np.sqrt(np.sum(diffs ** 2, axis=1))
            total_length = float(np.sum(seg_lengths))

            primitives: List = []

            if num_points == 2:
                dx = float(coords[1, 0] - coords[0, 0])
                dy = float(coords[1, 1] - coords[0, 1])
                heading = float(np.arctan2(dy, dx))
                primitives.append(LinePrimitive(
                    s=0.0, x=float(coords[0, 0]), y=float(coords[0, 1]),
                    heading=heading, length=total_length
                ))
                return PlanViewGeometry(length=total_length, primitives=primitives)

            curvatures = []
            for i in range(1, num_points - 1):
                p1, p2, p3 = coords[i - 1], coords[i], coords[i + 1]
                a = np.linalg.norm(p1 - p2)
                b = np.linalg.norm(p2 - p3)
                c = np.linalg.norm(p3 - p1)
                area = 0.5 * abs(
                    p1[0] * (p2[1] - p3[1]) +
                    p2[0] * (p3[1] - p1[1]) +
                    p3[0] * (p1[1] - p2[1])
                )
                denom = a * b * c
                kappa = (4.0 * area / denom) if denom > 1e-10 else 0.0
                cross = (p2[0] - p1[0]) * (p3[1] - p1[1]) - (p2[1] - p1[1]) * (p3[0] - p1[0])
                kappa = kappa if cross >= 0 else -kappa
                curvatures.append(kappa)

            avg_curvature = float(np.mean(np.abs(curvatures)))
            dx = float(coords[-1, 0] - coords[0, 0])
            dy = float(coords[-1, 1] - coords[0, 1])
            start_heading = float(np.arctan2(dy, dx))

            if avg_curvature < 0.001:
                primitives.append(LinePrimitive(
                    s=0.0, x=float(coords[0, 0]), y=float(coords[0, 1]),
                    heading=start_heading, length=total_length
                ))
            else:
                mean_kappa = float(np.mean(curvatures))
                dx0 = float(coords[1, 0] - coords[0, 0])
                dy0 = float(coords[1, 1] - coords[0, 1])
                h0 = float(np.arctan2(dy0, dx0))
                primitives.append(ArcPrimitive(
                    s=0.0, x=float(coords[0, 0]), y=float(coords[0, 1]),
                    heading=h0, length=total_length, curvature=mean_kappa
                ))

            return PlanViewGeometry(length=total_length, primitives=primitives)

        except Exception as e:
            print(f"[Geometry] PrimitiveFitting failed for '{segment.id}' ({e}). Using ExactPolylineStrategy.")
            return ExactPolylineStrategy().fit(segment)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def segment_to_planview(
    segment: AnchoredOvertureSegment,
    strategy: GeometryStrategy = None
) -> PlanViewGeometry:
    """
    Convert a segment to a PlanViewGeometry.
    Defaults to ExactPolylineStrategy — geometrically exact, no fitting required.
    """
    if strategy is None:
        strategy = ExactPolylineStrategy()
    return strategy.fit(segment)
