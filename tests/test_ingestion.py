from datetime import datetime, timezone

from ofi.domain.models import CropCycle, Farm, GeoPoint, Observation, Parcel
from ofi.providers.base import EvidenceProvider
from ofi.services.ingestion import EvidenceIngestionService
from ofi.twin.farm_twin import FarmTwinStore


class GoodProvider(EvidenceProvider):
    name = "good"
    capabilities = frozenset({"weather"})

    def collect(self, snapshot):
        return [Observation(
            id="obs-good",
            kind="weather",
            timestamp=datetime.now(timezone.utc),
            value={"temperature_c": 34},
            source="weather_station",
            crop_cycle_id=snapshot.active_crop.id,
            location=snapshot.parcel_location,
        )]


class BrokenProvider(EvidenceProvider):
    name = "broken"
    capabilities = frozenset({"satellite"})

    def collect(self, snapshot):
        raise TimeoutError("upstream unavailable")


def farm():
    return Farm(
        id="farm-1",
        farmer_id="farmer-1",
        parcel=Parcel(
            id="parcel-1",
            location=GeoPoint(latitude=27, longitude=72),
            boundary=[
                GeoPoint(latitude=27, longitude=72),
                GeoPoint(latitude=27, longitude=72.01),
                GeoPoint(latitude=27.01, longitude=72.01),
            ],
        ),
        crop_cycle=CropCycle(id="crop-1", crop="bajra"),
    )


def test_ingestion_persists_success_and_isolates_provider_failure():
    store = FarmTwinStore()
    store.upsert(farm())

    report = EvidenceIngestionService(
        store, [GoodProvider(), BrokenProvider()]
    ).ingest("farm-1")

    assert report.successful_providers == ["good"]
    assert len(report.observations) == 1
    assert len(report.failures) == 1
    assert "TimeoutError" in report.failures[0].error
    assert store.snapshot("farm-1").recent_observations[0].id == "obs-good"
