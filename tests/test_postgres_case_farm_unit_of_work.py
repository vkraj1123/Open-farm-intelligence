"""Integration tests for the shared PostgreSQL case + farm transaction."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from ofi.domain.models import (
    CropCycle,
    Farm,
    FarmCase,
    GeoPoint,
    Observation,
    Parcel,
)
from ofi.services.case_manager import CaseManager
from ofi.services.unit_of_work import PostgresFarmCaseUnitOfWork
from ofi.twin.migrate import apply_schema


DATABASE_URL = os.environ.get("OFI_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires PostgreSQL CI service via OFI_POSTGRES_DSN",
)


@pytest.fixture()
def connection_factory():
    assert apply_schema(DATABASE_URL) >= 4
    return lambda: psycopg.connect(DATABASE_URL)


def make_case(*, include_observation=False):
    suffix = uuid4().hex
    crop_id = f"crop-{suffix}"
    observations = []
    if include_observation:
        observations.append(
            Observation(
                id=f"observation-{suffix}",
                kind="farmer_report",
                timestamp=datetime.now(timezone.utc),
                value={"symptom": "yellowing"},
                source="farmer",
                crop_cycle_id=crop_id,
            )
        )
    return FarmCase(
        id=f"case-{suffix}",
        farm=Farm(
            id=f"farm-{suffix}",
            farmer_id=f"farmer-{suffix}",
            parcel=Parcel(
                id=f"parcel-{suffix}",
                location=GeoPoint(latitude=27.0, longitude=72.0),
            ),
            crop_cycle=CropCycle(id=crop_id, crop="bajra"),
        ),
        query="PostgreSQL persistence integration",
        observations=observations,
    )


def make_manager(connection_factory):
    return CaseManager(
        unit_of_work=PostgresFarmCaseUnitOfWork(connection_factory)
    )


def test_case_and_farm_twin_survive_manager_recreation(connection_factory):
    case = make_case(include_observation=True)
    manager = make_manager(connection_factory)
    created = manager.create(case)

    reloaded_manager = make_manager(connection_factory)
    reloaded_case = reloaded_manager.store.get(case.id)
    snapshot = reloaded_manager.farm_twin.snapshot(case.farm.id)

    assert created.case.id == reloaded_case.case.id
    assert reloaded_case.case.events[0].event_type == "reported"
    assert snapshot.farm_id == case.farm.id
    assert [item.id for item in snapshot.recent_observations] == [
        item.id for item in case.observations
    ]


def test_case_and_farm_writes_roll_back_together_on_twin_failure(
    connection_factory, monkeypatch
):
    case = make_case(include_observation=True)
    manager = make_manager(connection_factory)

    def fail_observation_write(farm_id, observation, *, tenant_id=None):
        raise RuntimeError("injected farm-twin failure")

    monkeypatch.setattr(manager.farm_twin, "add_observation", fail_observation_write)
    with pytest.raises(RuntimeError, match="injected farm-twin failure"):
        manager.create(case)

    with psycopg.connect(DATABASE_URL) as connection:
        case_count = connection.execute(
            "SELECT count(*) FROM case_records WHERE id=%s", (case.id,)
        ).fetchone()[0]
        farm_count = connection.execute(
            "SELECT count(*) FROM farms WHERE id=%s", (case.farm.id,)
        ).fetchone()[0]

    assert case_count == 0
    assert farm_count == 0
