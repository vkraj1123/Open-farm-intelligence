from datetime import datetime, timedelta, timezone

import pytest

from ofi.domain.models import CropCycle, FarmSnapshot, GeoPoint, Observation
from ofi.geospatial.analytics import (
    parcel_ndvi_observation,
    select_recent_satellite_observation,
)
from ofi.geospatial.spatial import RasterPixel


def snapshot():
    return FarmSnapshot(
        farm_id="f", as_of=datetime.now(timezone.utc),
        active_crop=CropCycle(id="c", crop="bajra"),
        parcel_location=GeoPoint(latitude=27.005, longitude=72.005),
        parcel_boundary=[
            GeoPoint(latitude=27.0, longitude=72.0),
            GeoPoint(latitude=27.0, longitude=72.01),
            GeoPoint(latitude=27.01, longitude=72.01),
            GeoPoint(latitude=27.01, longitude=72.0),
        ],
    )


def test_select_recent_satellite_observation_respects_freshness():
    now = datetime.now(timezone.utc)
    old = Observation(
        id="old", kind="satellite", timestamp=now - timedelta(days=20),
        value={"ndvi": 0.2}, source="sentinel-2",
    )
    recent = Observation(
        id="recent", kind="satellite", timestamp=now - timedelta(days=2),
        value={"ndvi": 0.5}, source="sentinel-2",
    )
    assert select_recent_satellite_observation([old, recent], as_of=now).id == "recent"


def test_select_recent_satellite_observation_returns_none_when_stale():
    now = datetime.now(timezone.utc)
    old = Observation(
        id="old", kind="satellite", timestamp=now - timedelta(days=20),
        value={"ndvi": 0.2}, source="sentinel-2",
    )
    assert select_recent_satellite_observation([old], as_of=now) is None


def test_parcel_ndvi_observation_uses_only_valid_parcel_pixels():
    obs = parcel_ndvi_observation(
        snapshot(),
        [
            RasterPixel(GeoPoint(latitude=27.002, longitude=72.002), 0.4),
            RasterPixel(GeoPoint(latitude=27.006, longitude=72.006), 0.6),
            RasterPixel(GeoPoint(latitude=27.004, longitude=72.004), 0.8, cloud=True),
            RasterPixel(GeoPoint(latitude=27.02, longitude=72.02), 0.99),
        ],
        scene_id="S2A_001",
        acquisition_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_id="scene-001",
        cloud_cover_pct=10,
    )
    assert obs.value["ndvi"] == 0.5
    assert obs.value["median_ndvi"] == 0.5
    assert obs.value["pixel_count"] == 3
    assert obs.value["valid_pixel_fraction"] == 2 / 3
    assert obs.value["cloud_fraction"] == 1 / 3
    assert obs.provenance["scene_id"] == "S2A_001"
    assert obs.spatial_scope == "parcel"
    assert obs.quality == round(2 / 3 * 2 / 3, 3)


def test_parcel_ndvi_observation_requires_valid_pixels():
    with pytest.raises(ValueError):
        parcel_ndvi_observation(
            snapshot(),
            [RasterPixel(GeoPoint(latitude=27.004, longitude=72.004), 0.8, cloud=True)],
            scene_id="S2A_002",
            acquisition_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
