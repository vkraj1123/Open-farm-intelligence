from datetime import datetime, timezone

from ofi.domain.models import CaseOutcome, CropCycle, Farm, FarmCase, GeoPoint, Observation, Parcel
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
    assert len(r.case.observations) == 3
    assert {item.kind for item in r.case.observations} == {"weather", "satellite", "soil"}
    snapshot = m.farm_twin.snapshot("f1")
    assert len(snapshot.recent_observations) == 3


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
    assert snapshot := m.farm_twin.snapshot("f1")
    assert snapshot.recent_observations[0].id == "outcome-1"


def test_duplicate_case_rejected():
    m = CaseManager()
    m.create(case())
    try:
        m.create(case())
    except ValueError:
        return
    assert False
