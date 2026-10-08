from dataclasses import dataclass
from math import cos, radians, sqrt
from statistics import mean, median
from typing import Iterable

from ofi.domain.models import GeoPoint, Parcel


@dataclass(frozen=True)
class SpatialAlignment:
    match: float
    usable: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpatialFootprint:
    """Provider-neutral polygon footprint for a scene, tile, or analysis window."""
    points: tuple[GeoPoint, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("spatial footprint requires at least three points")


@dataclass(frozen=True)
class RasterPixel:
    location: GeoPoint
    value: float
    valid: bool = True
    cloud: bool = False


@dataclass(frozen=True)
class ParcelRasterSummary:
    mean: float | None
    median: float | None
    valid_pixel_fraction: float
    cloud_fraction: float
    pixel_count: int


def _project(point: GeoPoint, reference_lat: float) -> tuple[float, float]:
    """Project lat/lon to local kilometres for small agricultural polygons."""
    x = radians(point.longitude) * 6371.0 * cos(radians(reference_lat))
    y = radians(point.latitude) * 6371.0
    return x, y


def _cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def point_in_polygon(point: GeoPoint, polygon: Iterable[GeoPoint]) -> bool:
    points = list(polygon)
    if len(points) < 3:
        return False
    reference_lat = point.latitude
    p = _project(point, reference_lat)
    inside = False
    for index, a_geo in enumerate(points):
        b_geo = points[(index + 1) % len(points)]
        a = _project(a_geo, reference_lat)
        b = _project(b_geo, reference_lat)
        if _point_on_segment(p, a, b):
            return True
        if (a[1] > p[1]) != (b[1] > p[1]):
            x_at_y = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if p[0] < x_at_y:
                inside = not inside
    return inside


def _point_on_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float], tolerance: float = 1e-9
) -> bool:
    if abs(_cross(a, b, p)) > tolerance:
        return False
    return (
        min(a[0], b[0]) - tolerance <= p[0] <= max(a[0], b[0]) + tolerance
        and min(a[1], b[1]) - tolerance <= p[1] <= max(a[1], b[1]) + tolerance
    )


def _segments_intersect(
    a: tuple[float, float], b: tuple[float, float],
    c: tuple[float, float], d: tuple[float, float]
) -> bool:
    c1 = _cross(a, b, c)
    c2 = _cross(a, b, d)
    c3 = _cross(c, d, a)
    c4 = _cross(c, d, b)
    if ((c1 > 0) != (c2 > 0)) and ((c3 > 0) != (c4 > 0)):
        return True
    return (
        (abs(c1) < 1e-9 and _point_on_segment(c, a, b))
        or (abs(c2) < 1e-9 and _point_on_segment(d, a, b))
        or (abs(c3) < 1e-9 and _point_on_segment(a, c, d))
        or (abs(c4) < 1e-9 and _point_on_segment(b, c, d))
    )


def polygons_overlap(left: Iterable[GeoPoint], right: Iterable[GeoPoint]) -> bool:
    a = list(left)
    b = list(right)
    if len(a) < 3 or len(b) < 3:
        return False
    reference_lat = mean([p.latitude for p in a + b])
    pa = [_project(p, reference_lat) for p in a]
    pb = [_project(p, reference_lat) for p in b]
    for i, start in enumerate(pa):
        end = pa[(i + 1) % len(pa)]
        for j, other_start in enumerate(pb):
            other_end = pb[(j + 1) % len(pb)]
            if _segments_intersect(start, end, other_start, other_end):
                return True
    return point_in_polygon(a[0], b) or point_in_polygon(b[0], a)


def parcel_spatial_alignment(parcel: Parcel, location: GeoPoint | None) -> SpatialAlignment:
    if location is None:
        return SpatialAlignment(0.7, True, ("observation_location_unknown",))
    if not parcel.boundary:
        return SpatialAlignment(0.7, True, ("parcel_boundary_unknown",))
    if point_in_polygon(location, parcel.boundary):
        return SpatialAlignment(1.0, True)
    return SpatialAlignment(0.0, False, ("observation_outside_parcel",))


def footprint_overlaps_parcel(parcel: Parcel, footprint: SpatialFootprint) -> bool:
    if not parcel.boundary:
        return False
    return polygons_overlap(parcel.boundary, footprint.points)


def aggregate_pixels_to_parcel(
    parcel: Parcel, pixels: Iterable[RasterPixel]
) -> ParcelRasterSummary:
    candidates = list(pixels)
    if not candidates:
        return ParcelRasterSummary(None, None, 0.0, 0.0, 0)

    inside = [p for p in candidates if parcel_spatial_alignment(parcel, p.location).usable and (
        not parcel.boundary or point_in_polygon(p.location, parcel.boundary)
    )]
    if not inside:
        return ParcelRasterSummary(None, None, 0.0, 0.0, 0)

    valid = [p for p in inside if p.valid and not p.cloud]
    cloudy = [p for p in inside if p.cloud]
    values = [p.value for p in valid]
    return ParcelRasterSummary(
        mean=mean(values) if values else None,
        median=median(values) if values else None,
        valid_pixel_fraction=len(valid) / len(inside),
        cloud_fraction=len(cloudy) / len(inside),
        pixel_count=len(inside),
    )
