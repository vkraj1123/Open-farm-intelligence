from datetime import datetime, timedelta, timezone

from ofi.domain.models import CropCycle, Farm, GeoPoint, Observation, Parcel
from ofi.providers.base import EvidenceProvider
from ofi.services.evidence_coordinator import FarmEvidenceCoordinator
from ofi.twin.farm_twin import FarmTwinStore


class Provider(EvidenceProvider):
    name = "weather"
    capabilities = frozenset({"weather"})

    def collect(self, snapshot):
        return [Observation(
            id=f"weather-{int(datetime.now(timezone.utc).timestamp())}",
            kind="weather",
            timestamp=snapshot.as_of,
            value={"temperature_c": 34},
            source="weather_model",
            crop_cycle_id=snapshot.active_crop.id,
        )]


def make_store():
    store = FarmTwinStore()
    store.upsert(Farm(
        id="f1",
        farmer_id="u1",
        parcel=Parcel(
            id="p1",
            location=GeoPoint(latitude=27, longitude=72),
            boundary=[
                GeoPoint(latitude=27, longitude=72),
                GeoPoint(latitude=27, longitude=72.01),
                GeoPoint(latitude=27.01, longitude=72.01),
            ],
        ),
        crop_cycle=CropCycle(id="c1", crop="bajra"),
    ))
    return store


def test_coordinator_plans_missing_evidence():
    store = make_store()
    plan = FarmEvidenceCoordinator(store, [Provider()]).plan("f1")
    assert "weather" in plan.due
    assert "satellite" in plan.due


def test_coordinator_skips_refresh_when_all_required_evidence_is_fresh():
    store = make_store()
    now = datetime.now(timezone.utc)
    store.add_observation("f1", Observation(
        id="weather-1",
        kind="weather",
        timestamp=now - timedelta(hours=2),
        value={"temperature_c": 34},
        source="weather_model",
    ))
    store.add_observation("f1", Observation(
        id="sat-1",
        kind="satellite",
        timestamp=now - timedelta(days=2),
        value={"ndvi": 0.4},
        source="sentinel-2",
    ))
    store.add_observation("f1", Observation(
        id="soil-1",
        kind="soil",
        timestamp=now - timedelta(days=10),
        value={"moisture_pct": 20},
        source="soil_lab",
    ))
    store.add_observation("f1", Observation(
        id="sensor-1",
        kind="sensor",
        timestamp=now - timedelta(days=2),
        value={"moisture_pct": 20},
        source="calibrated_sensor",
    ))
    store.add_observation("f1", Observation(
        id="market-1",
        kind="market",
        timestamp=now - timedelta(hours=12),
        value={"price": 3000},
        source="market",
    ))
    plan = FarmEvidenceCoordinator(store, [Provider()]).plan("f1", as_of=now)
    assert plan.is_current
