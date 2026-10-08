from datetime import date, datetime, timezone

from ofi.domain.models import CropCycle, FarmSnapshot, GeoPoint
from ofi.providers.base import ProviderRegistry
from ofi.providers.mock_evidence import MockSatelliteProvider, MockSoilProvider, MockWeatherProvider
from ofi.providers.normalizers import satellite_observation


def snapshot():
    return FarmSnapshot(
        farm_id="farm-1",
        as_of=datetime.now(timezone.utc),
        parcel_location=GeoPoint(latitude=27.0, longitude=72.0),
        active_crop=CropCycle(id="crop-1", crop="bajra", sowing_date=date(2026, 7, 1)),
    )


def test_registry_collects_from_all_providers():
    registry = ProviderRegistry([MockWeatherProvider(), MockSatelliteProvider(), MockSoilProvider()])
    observations = registry.collect(snapshot())
    assert {item.kind for item in observations} == {"weather", "satellite", "soil"}
    assert all(item.crop_cycle_id == "crop-1" for item in observations)


def test_provider_capability_lookup():
    registry = ProviderRegistry([MockWeatherProvider(), MockSatelliteProvider(), MockSoilProvider()])
    assert [item.name for item in registry.providers_for("vegetation_index")] == ["mock_satellite"]
    assert registry.providers_for("market") == []


def test_satellite_normalizer_rejects_empty_payload():
    try:
        satellite_observation(snapshot(), provider="test", payload={})
    except ValueError as exc:
        assert "vegetation indices" in str(exc)
        return
    assert False


def test_canonical_satellite_adapter_preserves_provenance():
    from ofi.providers.adapters import SatelliteAdapter

    adapter = SatelliteAdapter(lambda _: {
        "timestamp": "2026-08-15T06:00:00+00:00",
        "source": "sentinel-2",
        "source_id": "S2_TILE_001",
        "values": {"ndvi": 0.52, "ndwi": -0.05, "cloud_cover_pct": 4},
        "spatial_scope": "parcel",
        "quality": 0.92,
        "confidence": 0.88,
    })
    obs = adapter.collect(snapshot())[0]
    assert obs.source == "sentinel-2"
    assert obs.provenance["provider_source_id"] == "S2_TILE_001"
    assert obs.spatial_scope == "parcel"
    assert obs.value["ndvi"] == 0.52


def test_canonical_adapter_rejects_missing_required_value():
    from ofi.providers.adapters import AdapterError, canonical_payload

    try:
        canonical_payload({"source": "test"}, required=("ndvi",))
    except AdapterError as exc:
        assert "ndvi" in str(exc)
        return
    assert False


def test_weather_normalizer_preserves_scientific_fields():
    from ofi.providers.normalizers import weather_observation

    obs = weather_observation(snapshot(), provider="weather", payload={
        "timestamp": "2026-08-15T06:00:00+00:00",
        "rainfall_mm_last_7d": 12,
        "temperature_c": 34,
        "temperature_min_c": 25,
        "temperature_max_c": 39,
        "humidity_min_pct": 28,
        "humidity_max_pct": 62,
        "solar_mj_m2_day": 23.5,
        "wind_speed_ms": 3.2,
        "spatial_scope": "parcel",
    })
    assert obs.value["temperature_min_c"] == 25
    assert obs.value["temperature_max_c"] == 39
    assert obs.value["solar_mj_m2_day"] == 23.5
    assert obs.spatial_scope == "parcel"
    assert obs.location == snapshot().parcel_location
