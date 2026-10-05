from datetime import date, datetime, timezone

from ofi.domain.models import CropCycle, FarmSnapshot
from ofi.providers.base import ProviderRegistry
from ofi.providers.mock_evidence import MockSatelliteProvider, MockSoilProvider, MockWeatherProvider
from ofi.providers.normalizers import satellite_observation


def snapshot():
    return FarmSnapshot(
        farm_id="farm-1",
        as_of=datetime.now(timezone.utc),
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
