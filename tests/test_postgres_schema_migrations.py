"""PostgreSQL integration tests for ordered, checksummed schema migrations."""

import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from ofi.services.database_readiness import CURRENT_SCHEMA_VERSION, check_persistence_readiness
from ofi.twin.migrate import apply_schema
from ofi.twin.migration_registry import MIGRATIONS


DATABASE_URL = os.environ.get("OFI_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires PostgreSQL/PostGIS CI service via OFI_POSTGRES_DSN",
)


def test_migrations_record_in_order_and_reapply_without_changes():
    assert apply_schema(DATABASE_URL) == CURRENT_SCHEMA_VERSION
    assert apply_schema(DATABASE_URL) == CURRENT_SCHEMA_VERSION

    with psycopg.connect(DATABASE_URL) as connection:
        rows = connection.execute(
            "SELECT version, name, checksum FROM ofi_schema_migrations ORDER BY version"
        ).fetchall()

    assert [row[0] for row in rows] == [item.version for item in MIGRATIONS]
    assert [row[1] for row in rows] == [item.name for item in MIGRATIONS]
    assert [row[2] for row in rows] == [item.checksum for item in MIGRATIONS]


def test_legacy_v1_bootstrap_applies_pending_v2_migration():
    # Prior releases applied schema.sql directly and stamped only version 1.
    schema_path = Path(__file__).parents[1] / "src" / "ofi" / "twin" / "schema.sql"
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(schema_path.read_text(encoding="utf-8"))
        connection.execute(
            "ALTER TABLE case_records DROP CONSTRAINT IF EXISTS "
            "case_records_version_nonnegative"
        )
        connection.execute("DELETE FROM ofi_schema_migrations WHERE version = 2")
        # Emulate the pre-checksum migration ledger schema from the last release.
        connection.execute(
            "ALTER TABLE ofi_schema_migrations DROP COLUMN IF EXISTS checksum"
        )

    assert apply_schema(DATABASE_URL) == CURRENT_SCHEMA_VERSION

    with psycopg.connect(DATABASE_URL) as connection:
        versions = connection.execute(
            "SELECT version FROM ofi_schema_migrations ORDER BY version"
        ).fetchall()
        constraint = connection.execute(
            """
            SELECT count(*)
            FROM pg_constraint
            WHERE conname = 'case_records_version_nonnegative'
              AND conrelid = 'case_records'::regclass
            """
        ).fetchone()[0]

    assert [row[0] for row in versions] == [1, 2]
    assert constraint == 1


def test_readiness_passes_after_all_migrations_are_applied():
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


def test_applied_migration_checksum_mismatch_fails_closed():
    apply_schema(DATABASE_URL)
    migration = MIGRATIONS[-1]
    try:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "UPDATE ofi_schema_migrations SET checksum=%s WHERE version=%s",
                ("0" * 64, migration.version),
            )

        with pytest.raises(RuntimeError, match="Checksum mismatch"):
            apply_schema(DATABASE_URL)
    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "UPDATE ofi_schema_migrations SET checksum=%s WHERE version=%s",
                (migration.checksum, migration.version),
            )
