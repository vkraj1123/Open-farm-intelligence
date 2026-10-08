import os
from datetime import date
from pathlib import Path

import pytest
import psycopg

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


pytestmark = pytest.mark.integration


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
