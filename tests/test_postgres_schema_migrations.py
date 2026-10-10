"""PostgreSQL integration tests for schema version tracking and readiness."""

import os

import pytest

psycopg = pytest.importorskip("psycopg")

from ofi.services.database_readiness import CURRENT_SCHEMA_VERSION, check_persistence_readiness
from ofi.twin.migrate import apply_schema


DATABASE_URL = os.environ.get("OFI_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires PostgreSQL/PostGIS CI service via OFI_POSTGRES_DSN",
)


def test_schema_migration_records_version_and_is_idempotent():
    assert apply_schema(DATABASE_URL) == CURRENT_SCHEMA_VERSION
    assert apply_schema(DATABASE_URL) == CURRENT_SCHEMA_VERSION

    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT version, name FROM ofi_schema_migrations WHERE version=%s",
            (CURRENT_SCHEMA_VERSION,),
        ).fetchone()

    assert row is not None
    assert row[0] == CURRENT_SCHEMA_VERSION
    assert row[1]


def test_readiness_passes_after_schema_is_applied():
    apply_schema(DATABASE_URL)

    report = check_persistence_readiness(lambda: psycopg.connect(DATABASE_URL))

    assert report["status"] == "ready"
    assert report["persistence"] == "postgresql"
    assert report["checks"]["schema_version"]["current"] == CURRENT_SCHEMA_VERSION
    assert report["checks"]["missing_relations"] == []


def test_migration_refuses_to_downgrade_a_newer_schema_marker():
    apply_schema(DATABASE_URL)
    future_version = CURRENT_SCHEMA_VERSION + 1000
    try:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "INSERT INTO ofi_schema_migrations(version, name) VALUES (%s, %s)",
                (future_version, "synthetic_future_version"),
            )

        with pytest.raises(RuntimeError, match="newer than this application supports"):
            apply_schema(DATABASE_URL)
    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "DELETE FROM ofi_schema_migrations WHERE version=%s",
                (future_version,),
            )
