from ofi.domain.models import GeoPoint, Parcel
from ofi.geospatial.spatial import (
    RasterPixel,
    SpatialFootprint,
    aggregate_pixels_to_parcel,
    footprint_overlaps_parcel,
    parcel_spatial_alignment,
    point_in_polygon,
)


def parcel():
    return Parcel(
        id="p",
        location=GeoPoint(latitude=27.0, longitude=72.0),
        boundary=[
            GeoPoint(latitude=27.0, longitude=72.0),
            GeoPoint(latitude=27.0, longitude=72.01),
            GeoPoint(latitude=27.01, longitude=72.01),
            GeoPoint(latitude=27.01, longitude=72.0),
        ],
    )


def test_point_inside_parcel():
    p = parcel()
    point = GeoPoint(latitude=27.005, longitude=72.005)
    assert point_in_polygon(point, p.boundary)
    assert parcel_spatial_alignment(p, point).usable


def test_point_outside_parcel_is_rejected():
    p = parcel()
    point = GeoPoint(latitude=27.02, longitude=72.005)
    result = parcel_spatial_alignment(p, point)
    assert result.usable is False
    assert "observation_outside_parcel" in result.reasons


def test_boundary_point_is_treated_as_inside():
    p = parcel()
    point = GeoPoint(latitude=27.0, longitude=72.005)
    assert point_in_polygon(point, p.boundary)


def test_scene_footprint_overlap():
    p = parcel()
    footprint = SpatialFootprint((
        GeoPoint(latitude=26.995, longitude=71.995),
        GeoPoint(latitude=26.995, longitude=72.005),
        GeoPoint(latitude=27.005, longitude=72.005),
        GeoPoint(latitude=27.005, longitude=71.995),
    ))
    assert footprint_overlaps_parcel(p, footprint)


def test_scene_footprint_without_overlap():
    p = parcel()
    footprint = SpatialFootprint((
        GeoPoint(latitude=27.02, longitude=72.02),
        GeoPoint(latitude=27.03, longitude=72.02),
        GeoPoint(latitude=27.03, longitude=72.03),
        GeoPoint(latitude=27.02, longitude=72.03),
    ))
    assert footprint_overlaps_parcel(p, footprint) is False


def test_raster_pixels_aggregate_only_inside_parcel():
    p = parcel()
    summary = aggregate_pixels_to_parcel(p, [
        RasterPixel(GeoPoint(latitude=27.002, longitude=72.002), 0.4),
        RasterPixel(GeoPoint(latitude=27.006, longitude=72.006), 0.6),
        RasterPixel(GeoPoint(latitude=27.02, longitude=72.02), 0.99),
        RasterPixel(GeoPoint(latitude=27.004, longitude=72.004), 0.8, cloud=True),
    ])
    assert summary.pixel_count == 3
    assert summary.mean == 0.5
    assert summary.valid_pixel_fraction == 2 / 3
    assert summary.cloud_fraction == 1 / 3
