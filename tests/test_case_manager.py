from datetime import datetime, timedelta, timezone

from ofi.domain.models import CaseOutcome, CropCycle, Farm, FarmCase, GeoPoint, Observation, Parcel
from ofi.providers.base import EvidenceProvider
from ofi.providers.registry import default_mock_registry
from ofi.services.case_manager import CaseManager


def case():
    return FarmCase(
        id="c1",
        farm=Farm(
            id="f1",
            farmer_id="u1",
            parcel=Parcel(id="p1", location=GeoPoint(latitude=27, longitude=72)),
            crop_cycle=CropCycle(id="crop-1", crop="bajra"),
        ),
        query="yellowing",
    )




class HistoricalSatelliteProvider(EvidenceProvider):
    name = "historical_satellite"
    capabilities = frozenset({"satellite", "vegetation_index"})

    def collect(self, snapshot):
        now = datetime.now(timezone.utc)
        return [
            Observation(id="sat-old", kind="satellite", timestamp=now - timedelta(days=10),
                        value={"ndvi": 0.58, "ndwi": 0.04}, source="sentinel",
                        crop_cycle_id=snapshot.active_crop.id),
            Observation(id="sat-new", kind="satellite", timestamp=now,
                        value={"ndvi": 0.38, "ndwi": -0.12}, source="sentinel",
                        crop_cycle_id=snapshot.active_crop.id),
        ]

def test_case_lifecycle():
    m = CaseManager()
    r = m.create(case())
    assert r.case.status == "reported"

    r = m.add_observation(
        "c1",
        Observation(
            id="o1",
            kind="farmer_report",
            timestamp=datetime.now(timezone.utc),
            value={"symptom": "yellowing"},
            source="farmer_report",
        ),
    )
    assert r.case.status == "triaged" and len(r.case.events) == 2

    r = m.record_outcome("c1", CaseOutcome(outcome="improved", notes="better"))
    assert r.case.status == "observing" and r.outcome.outcome == "improved"


def test_provider_evidence_enters_case_and_twin():
    m = CaseManager()
    m.create(case())
    r = m.collect_evidence("c1", default_mock_registry())
    assert len(r.case.observations) == 4
    assert {"weather", "satellite", "soil", "model"} <= {item.kind for item in r.case.observations}
    reasoning = m.reason("c1")
    assert reasoning.latest_reasoning is not None
    assert reasoning.latest_reasoning.decision.action in {"REQUEST_TEST", "ESCALATE_EXPERT", "ADVISE"}
    snapshot = m.farm_twin.snapshot("f1")
    assert len(snapshot.recent_observations) == 4



def test_historical_satellite_series_creates_derived_trend():
    m = CaseManager()
    m.create(case())
    registry = default_mock_registry()
    registry.register(HistoricalSatelliteProvider())
    r = m.collect_evidence("c1", registry)
    derived = [item for item in r.case.observations if item.source == "ofi_geospatial_analytics"]
    scientific = [item for item in r.case.observations if item.source == "ofi_fao56_water_balance"]
    assert len(derived) == 1
    assert len(scientific) == 1
    assert derived[0].value["ndvi_trend"] < 0
    assert scientific[0].value["stress_fraction"] >= 0

def test_outcome_evidence_enters_twin():
    m = CaseManager()
    m.create(case())
    evidence = Observation(
        id="outcome-1",
        kind="farmer_report",
        timestamp=datetime.now(timezone.utc),
        value={"leaf_color": "improved"},
        source="farmer_report",
    )
    m.record_outcome("c1", CaseOutcome(outcome="resolved", evidence=[evidence]))
    snapshot = m.farm_twin.snapshot("f1")
    assert snapshot.recent_observations[0].id == "outcome-1"


def test_duplicate_case_rejected():
    m = CaseManager()
    m.create(case())
    try:
        m.create(case())
    except ValueError:
        return
    assert False


def test_provider_provenance_is_retained():
    m = CaseManager()
    m.create(case())
    r = m.collect_evidence("c1", default_mock_registry())
    external = [item for item in r.case.observations if item.kind == "weather"][0]
    assert external.spatial_scope == "farm"
    assert external.provenance["provider"] == "mock_weather"


class FailingTwin(FarmTwinStore):
    def upsert(self, farm):
        raise RuntimeError("twin write failed")


class FailingScientificModel:
    def run(self, snapshot):
        raise RuntimeError("scientific model failed")


def test_create_rolls_back_case_when_twin_write_fails():
    m = CaseManager(farm_twin=FailingTwin())
    try:
        m.create(case())
    except RuntimeError:
        pass
    else:
        assert False, "twin failure should propagate"
    assert m.store._records == {}


def test_collect_evidence_rolls_back_case_and_twin_on_late_failure():
    m = CaseManager(scientific_models=FailingScientificModel())
    m.create(case())
    before_case = m.store.get("c1").model_copy(deep=True)
    before_twin = m.farm_twin.snapshot("f1")

    try:
        m.collect_evidence("c1", default_mock_registry())
    except RuntimeError:
        pass
    else:
        assert False, "late evidence failure should propagate"

    after_case = m.store.get("c1")
    after_twin = m.farm_twin.snapshot("f1")
    assert after_case.case.model_dump(mode="json") == before_case.case.model_dump(mode="json")
    assert after_case.version == before_case.version
    assert after_twin.recent_observations == before_twin.recent_observations
