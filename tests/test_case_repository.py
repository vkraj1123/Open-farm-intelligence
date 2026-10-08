from datetime import datetime, timezone

from ofi.domain.models import Farm, FarmCase, Parcel, CropCycle, GeoPoint
from ofi.services.case_repository import InMemoryCaseRepository


def make_case():
    return FarmCase(
        id="case-repo",
        farm=Farm(
            id="farm-repo",
            farmer_id="farmer-1",
            parcel=Parcel(
                id="parcel-1",
                location=GeoPoint(latitude=25.0, longitude=72.0),
            ),
            crop_cycle=CropCycle(id="crop-1", crop="bajra"),
        ),
        query="water stress",
    )


def test_repository_round_trips_case():
    repository = InMemoryCaseRepository()
    created = repository.create(make_case())
    loaded = repository.get("case-repo")
    assert loaded.case.id == created.case.id
    assert loaded.case.farm.id == "farm-repo"


def test_repository_rejects_duplicate_events():
    repository = InMemoryCaseRepository()
    repository.create(make_case())
    from ofi.domain.models import CaseEvent

    event = CaseEvent(
        id="evt-1",
        event_type="test",
        timestamp=datetime.now(timezone.utc),
        actor="test",
    )
    repository.append_event("case-repo", event)
    try:
        repository.append_event("case-repo", event)
        assert False, "duplicate event should be rejected"
    except ValueError:
        pass


def test_atomic_event_commit_updates_state_and_ledger_together():
    repository = InMemoryCaseRepository()
    record = repository.create(make_case())
    from ofi.domain.models import CaseEvent

    record.case.status = "triaged"
    event = CaseEvent(
        id="evt-1",
        event_type="triaged",
        actor="system",
    )
    repository.save_and_append_event(record, event)

    loaded = repository.get("case-repo")
    assert loaded.case.status == "triaged"
    assert [item.event_type for item in loaded.case.events] == ["triaged"]
