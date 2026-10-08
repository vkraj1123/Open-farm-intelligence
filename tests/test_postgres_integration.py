import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from ofi.domain.models import (
    CaseEvent,
    CropCycle,
    Farm,
    FarmCase,
    GeoPoint,
    Observation,
    Parcel,
)
from ofi.services.postgres_case_repository import PostgresCaseRepository
from ofi.services.unit_of_work import PostgresFarmCaseUnitOfWork
from ofi.twin.postgis import PostGISFarmTwinStore


pytestmark = pytest.mark.skipif(
    not os.getenv("OFI_POSTGRES_DSN"),
    reason="OFI_POSTGRES_DSN is required for PostgreSQL integration tests",
)


def _dsn() -> str:
    return os.environ["OFI_POSTGRES_DSN"]


@pytest.fixture()
def database():
    with psycopg.connect(_dsn()) as conn:
        conn.execute(
            (Path(__file__).parents[1] / "src/ofi/twin/schema.sql").read_text()
        )
        conn.commit()
    yield
    with psycopg.connect(_dsn()) as conn:
        conn.execute(
            "TRUNCATE case_events, case_records, observations, "
            "production_contracts, land_parties, crop_cycles, parcels, farms "
            "CASCADE"
        )
        conn.commit()


def make_farm() -> Farm:
    return Farm(
        id="farm-integration",
        farmer_id="farmer-1",
        parcel=Parcel(
            id="parcel-integration",
            location=GeoPoint(latitude=25.0, longitude=72.0),
        ),
        crop_cycle=CropCycle(
            id="crop-integration",
            crop="bajra",
            season="kharif",
            sowing_date=date(2026, 7, 1),
        ),
    )


def make_case(farm: Farm) -> FarmCase:
    return FarmCase(
        id="case-integration",
        farm=farm,
        query="water stress",
    )


def test_real_postgres_uow_rolls_back_case_and_farm_together(database):
    factory = lambda: psycopg.connect(_dsn())
    farm = make_farm()
    case = make_case(farm)

    case_repo = PostgresCaseRepository(factory)
    twin = PostGISFarmTwinStore(factory)
    case_repo.create(case)
    twin.upsert(farm)

    uow = PostgresFarmCaseUnitOfWork(factory)

    with pytest.raises(RuntimeError, match="force rollback"):
        with uow.atomic():
            record = uow.case_repository.get(case.id)
            record.case.status = "triaged"
            uow.case_repository.save_and_append_event(
                record,
                CaseEvent(event_type="triaged", actor="system"),
            )
            uow.farm_twin.add_observation(
                farm.id,
                Observation(
                    id="obs-rollback",
                    kind="weather",
                    timestamp=record.case.updated_at,
                    value={"rainfall_mm": 0},
                    source="integration-test",
                ),
            )
            raise RuntimeError("force rollback")

    restored = case_repo.get(case.id)
    snapshot = twin.snapshot(farm.id, restored.case.updated_at)

    assert restored.version == 0
    assert restored.case.status == "reported"
    assert restored.case.events == []
    assert all(obs.id != "obs-rollback" for obs in snapshot.recent_observations)


def _create_case_in_database(factory):
    farm = make_farm()
    case = make_case(farm)
    PostgresCaseRepository(factory).create(case)
    return farm, case


def test_real_postgres_rejects_stale_case_writer_without_partial_event(database):
    factory = lambda: psycopg.connect(_dsn())
    _, case = _create_case_in_database(factory)
    first_repo = PostgresCaseRepository(factory)
    second_repo = PostgresCaseRepository(factory)
    first = first_repo.get(case.id)
    second = second_repo.get(case.id)

    first.case.status = "triaged"
    second.case.status = "escalated"

    def commit(repo, record, event_type):
        try:
            repo.save_and_append_event(
                record,
                CaseEvent(event_type=event_type, actor="system"),
            )
            return "committed"
        except RuntimeError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda args: commit(*args),
            [
                (first_repo, first, "triaged"),
                (second_repo, second, "escalated"),
            ],
        ))

    assert sorted(result == "committed" for result in results) == [False, True]
    fresh = first_repo.get(case.id)
    assert fresh.version == 1
    assert len(fresh.case.events) == 1
    assert fresh.case.events[0].sequence == 1
    assert fresh.case.status in {"triaged", "escalated"}


def test_real_postgres_failed_append_preserves_caller_state(database):
    factory = lambda: psycopg.connect(_dsn())
    _, case = _create_case_in_database(factory)
    repo = PostgresCaseRepository(factory)
    record = repo.get(case.id)
    original_status = record.case.status
    original_version = record.version
    original_events = list(record.case.events)

    record.case.status = "triaged"
    event = CaseEvent(
        event_type="triaged",
        actor="system",
        sequence=99,
    )

    with pytest.raises(ValueError, match="expected 1"):
        repo.save_and_append_event(record, event)

    assert record.version == original_version
    assert record.case.status == "triaged"
    assert record.case.events == original_events

    fresh = repo.get(case.id)
    assert fresh.version == original_version
    assert fresh.case.status == original_status
    assert fresh.case.events == []
