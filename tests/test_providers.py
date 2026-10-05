from datetime import date, datetime, timezone

from ofi.domain.models import CropCycle, FarmSnapshot
from ofi.providers.base import ProviderRegistry
from ofi.providers.mock_evidence import MockSatelliteProvider, MockSoilProvider, MockWeatherProvider


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


def test_duplicate_provider_rejected():
    registry = ProviderRegistry([MockWeatherProvider()])
    try:
        registry.register(MockWeatherProvider())
    except ValueError:
        return
    assert False
