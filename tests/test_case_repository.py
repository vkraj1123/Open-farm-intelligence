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


def test_case_version_advances_with_committed_event():
    repository = InMemoryCaseRepository()
    record = repository.create(make_case())
    assert record.version == 0
    from ofi.domain.models import CaseEvent
    event = CaseEvent(event_type="test", actor="system")
    repository.save_and_append_event(record, event)
    assert record.version == 1
    assert event.id.startswith("evt-")


def test_generated_event_ids_are_unique():
    from ofi.domain.models import CaseEvent
    assert CaseEvent(event_type="a", actor="x").id != CaseEvent(event_type="a", actor="x").id


def test_event_sequence_is_monotonic_and_durable_in_memory():
    repository = InMemoryCaseRepository()
    record = repository.create(make_case())
    from ofi.domain.models import CaseEvent

    first = CaseEvent(event_type="first", actor="system")
    second = CaseEvent(event_type="second", actor="system")
    repository.save_and_append_event(record, first)
    repository.save_and_append_event(record, second)

    loaded = repository.get("case-repo")
    assert [event.sequence for event in loaded.case.events] == [1, 2]
    assert loaded.case.events[0].id != loaded.case.events[1].id


def test_explicit_event_sequence_cannot_skip_or_duplicate():
    repository = InMemoryCaseRepository()
    record = repository.create(make_case())
    from ofi.domain.models import CaseEvent

    repository.save_and_append_event(
        record, CaseEvent(sequence=1, event_type="first", actor="system")
    )
    try:
        repository.save_and_append_event(
            record, CaseEvent(sequence=3, event_type="third", actor="system")
        )
        assert False, "event sequence gaps should be rejected"
    except ValueError:
        pass
