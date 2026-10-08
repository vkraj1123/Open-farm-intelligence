from datetime import datetime, timedelta, timezone

from ofi.domain.models import CropCycle, FarmSnapshot, GeoPoint, Observation
from ofi.geospatial.analytics import derived_ndvi_observation, ndvi_trend, vegetation_stress_index


def observations():
    now = datetime.now(timezone.utc)
    return [
        Observation(id="s1", kind="satellite", timestamp=now - timedelta(days=10),
                    value={"ndvi": 0.58, "ndwi": 0.05}, source="sentinel"),
        Observation(id="s2", kind="satellite", timestamp=now,
                    value={"ndvi": 0.38, "ndwi": -0.12}, source="sentinel"),
    ]


def test_ndvi_trend_detects_decline():
    trend = ndvi_trend(observations())
    assert trend is not None and trend < 0


def test_vegetation_stress_is_bounded():
    value = vegetation_stress_index(observations())
    assert value is not None and 0 <= value <= 1


def test_derived_ndvi_observation():
    now = datetime.now(timezone.utc)
    snapshot = FarmSnapshot(
        farm_id="farm-1", as_of=now,
        parcel_location=GeoPoint(latitude=27.0, longitude=72.0),
        active_crop=CropCycle(id="crop-1", crop="bajra"),
        recent_observations=observations(),
    )
    derived = derived_ndvi_observation(snapshot)
    assert derived is not None
    assert derived.kind == "satellite"
    assert derived.source == "ofi_geospatial_analytics"
    assert derived.value["ndvi_trend"] < 0
