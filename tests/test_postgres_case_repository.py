from contextlib import contextmanager

from ofi.domain.models import CaseEvent, CaseRecord, CropCycle, Farm, FarmCase, GeoPoint, Parcel
from ofi.services.postgres_case_repository import PostgresCaseRepository


def make_case():
    return FarmCase(
        id="case-postgres",
        farm=Farm(
            id="farm-postgres",
            farmer_id="farmer-1",
            parcel=Parcel(
                id="parcel-1",
                location=GeoPoint(latitude=25.0, longitude=72.0),
            ),
            crop_cycle=CropCycle(id="crop-1", crop="bajra"),
        ),
        query="water stress",
    )


class FakeCursor:
    def __init__(self):
        self.rowcount = 1
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return (1,)


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    @contextmanager
    def transaction(self):
        yield self

    def cursor(self):
        return self.cursor_instance

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_postgres_commit_assigns_sequence_to_domain_event():
    connection = FakeConnection()
    repository = PostgresCaseRepository(lambda: connection)
    record = CaseRecord(case=make_case())

    # The repository's real SQL persistence is outside this unit test; retain
    # the real event-sequence allocation path and stub only the state update.
    repository._save_record = lambda conn, working: 1

    event = CaseEvent(event_type="triaged", actor="system")
    repository.save_and_append_event(record, event)

    assert record.version == 1
    assert record.case.events[0].sequence == 1
    assert record.case.events[0].id == event.id
