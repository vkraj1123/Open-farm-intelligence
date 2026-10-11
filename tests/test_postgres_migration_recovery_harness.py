"""Real PostgreSQL upgrade, rollback, and dump/restore rehearsal tests."""

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql
from psycopg.conninfo import make_conninfo

from ofi.twin.migrate import apply_schema
from ofi.twin.migration_registry import CURRENT_SCHEMA_VERSION
from ofi.twin.recovery_check import run_recovery_check


DATABASE_URL = os.environ.get("OFI_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires disposable PostgreSQL/PostGIS CI database via OFI_POSTGRES_DSN",
)
SCHEMA_PATH = Path(__file__).parents[1] / "src" / "ofi" / "twin" / "schema.sql"


def create_database(base_dsn, prefix):
    name = f"{prefix}_{uuid4().hex[:16]}"
    admin_dsn = make_conninfo(base_dsn, dbname="postgres")
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return name, make_conninfo(base_dsn, dbname=name), admin_dsn


def drop_database(admin_dsn, name):
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def seed_v1_source(dsn):
    now = datetime.now(timezone.utc)
    case_id = f"recovery-case-{uuid4().hex}"
    farm_id = f"recovery-farm-{uuid4().hex}"
    crop_id = f"recovery-crop-{uuid4().hex}"
    observation_id = f"recovery-observation-{uuid4().hex}"
    event_id = f"recovery-event-{uuid4().hex}"

    with psycopg.connect(dsn) as connection:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO farms (id, farmer_id) VALUES (%s, %s)",
            (farm_id, "recovery-test-farmer"),
        )
        connection.execute(
            """
            INSERT INTO crop_cycles (id, farm_id, crop, status)
            VALUES (%s, %s, %s, %s)
            """,
            (crop_id, farm_id, "bajra", "active"),
        )
        connection.execute(
            """
            INSERT INTO observations
              (id, farm_id, crop_cycle_id, kind, observed_at, source,
               quality, confidence, value, provenance)
            VALUES (%s, %s, %s, 'farmer_report', %s, 'recovery-test',
                    1.0, 0.9, '{"symptom":"yellowing"}'::jsonb,
                    '{"test":"staging-recovery"}'::jsonb)
            """,
            (observation_id, farm_id, crop_id, now),
        )
        connection.execute(
            """
            INSERT INTO case_records
              (id,status,created_at,updated_at,version,case_data,latest_reasoning,outcome)
            VALUES (%s,'reported',%s,%s,0,%s::jsonb,NULL,NULL)
            """,
            (
                case_id,
                now,
                now,
                '{"query":"recovery rehearsal","status":"reported"}',
            ),
        )
        connection.execute(
            """
            INSERT INTO case_events
              (id,case_id,sequence,event_type,occurred_at,actor,payload)
            VALUES (%s,%s,1,'reported',%s,'recovery-test',
                    '{"recovery_fixture":true}'::jsonb)
            """,
            (event_id, case_id, now),
        )
    return {
        "case_id": case_id,
        "farm_id": farm_id,
        "crop_cycle_id": crop_id,
        "observation_id": observation_id,
        "event_id": event_id,
    }


def test_backup_restore_and_v1_to_v2_upgrade_preserve_source_data():
    source_name, source_dsn, admin_dsn = create_database(DATABASE_URL, "ofi_recovery_source")
    restore_name, restore_dsn, _ = create_database(DATABASE_URL, "ofi_recovery_target")
    try:
        record_ids = seed_v1_source(source_dsn)
        report = run_recovery_check(source_dsn, restore_dsn)

        assert report["status"] == "passed"
        assert report["source_modified"] is False
        assert report["backup_restore_verified"] is True
        assert report["row_counts_preserved"] is True
        assert report["data_fingerprints_preserved"] is True
        assert report["source_schema_version"] == CURRENT_SCHEMA_VERSION - 1
        assert report["restored_schema_version_before_migration"] == CURRENT_SCHEMA_VERSION - 1
        assert report["upgraded_schema_version"] == CURRENT_SCHEMA_VERSION
        assert report["readiness"] == "ready"
        assert report["row_counts"]["case_records"] >= 1
        assert report["row_counts"]["case_events"] >= 1

        with psycopg.connect(source_dsn) as source, psycopg.connect(restore_dsn) as restored:
            for table, key in (
                ("case_records", "case_id"),
                ("farms", "farm_id"),
                ("crop_cycles", "crop_cycle_id"),
                ("observations", "observation_id"),
            ):
                identifier = record_ids[key]
                source_row = source.execute(
                    sql.SQL("SELECT to_jsonb(t) FROM {} AS t WHERE id = %s").format(
                        sql.Identifier(table)
                    ),
                    (identifier,),
                ).fetchone()
                restored_row = restored.execute(
                    sql.SQL("SELECT to_jsonb(t) FROM {} AS t WHERE id = %s").format(
                        sql.Identifier(table)
                    ),
                    (identifier,),
                ).fetchone()
                assert source_row is not None
                assert restored_row == source_row

            restored_event = restored.execute(
                "SELECT payload FROM case_events WHERE id=%s",
                (record_ids["event_id"],),
            ).fetchone()
            assert restored_event[0]["recovery_fixture"] is True

        # Rehearsal must not upgrade or otherwise mutate the source.
        with psycopg.connect(source_dsn) as source:
            source_version = source.execute(
                "SELECT max(version) FROM ofi_schema_migrations"
            ).fetchone()[0]
        assert source_version == CURRENT_SCHEMA_VERSION - 1
    finally:
        drop_database(admin_dsn, restore_name)
        drop_database(admin_dsn, source_name)


def test_failed_v2_migration_rolls_back_schema_and_marker():
    source_name, source_dsn, admin_dsn = create_database(DATABASE_URL, "ofi_migration_rollback")
    try:
        seed_v1_source(source_dsn)
        bad_case_id = f"negative-version-{uuid4().hex}"
        now = datetime.now(timezone.utc)
        with psycopg.connect(source_dsn) as connection:
            connection.execute(
                """
                INSERT INTO case_records
                  (id,status,created_at,updated_at,version,case_data,latest_reasoning,outcome)
                VALUES (%s,'reported',%s,%s,-1,'{}'::jsonb,NULL,NULL)
                """,
                (bad_case_id, now, now),
            )

        with pytest.raises(psycopg.errors.CheckViolation):
            apply_schema(source_dsn)

        # DDL, the v1 checksum backfill and v2 marker are in the same
        # transaction. A failed check constraint must leave them uncommitted.
        with psycopg.connect(source_dsn) as connection:
            versions = connection.execute(
                "SELECT version FROM ofi_schema_migrations ORDER BY version"
            ).fetchall()
            checksum = connection.execute(
                "SELECT checksum FROM ofi_schema_migrations WHERE version=1"
            ).fetchone()[0]
            constraint_count = connection.execute(
                """
                SELECT count(*) FROM pg_constraint
                WHERE conname='case_records_version_nonnegative'
                  AND conrelid='case_records'::regclass
                """
            ).fetchone()[0]
            assert [row[0] for row in versions] == [1]
            assert checksum is None
            assert constraint_count == 0
            connection.execute("DELETE FROM case_records WHERE id=%s", (bad_case_id,))

        assert apply_schema(source_dsn) == CURRENT_SCHEMA_VERSION
    finally:
        drop_database(admin_dsn, source_name)



def test_current_schema_scheduled_drill_emits_encrypted_backup(tmp_path):
    age_keygen = shutil.which("age-keygen")
    age = shutil.which("age")
    if not age_keygen or not age:
        pytest.skip("age and age-keygen are installed by the PostgreSQL CI workflow")

    source_name, source_dsn, admin_dsn = create_database(DATABASE_URL, "ofi_recovery_current")
    restore_name, restore_dsn, _ = create_database(DATABASE_URL, "ofi_recovery_current_target")
    try:
        seed_v1_source(source_dsn)
        assert apply_schema(source_dsn) == CURRENT_SCHEMA_VERSION

        key_path = tmp_path / "recovery-age-key.txt"
        subprocess.run(
            [age_keygen, "-o", str(key_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        key_text = key_path.read_text(encoding="utf-8")
        recipient_line = next(
            line for line in key_text.splitlines() if line.startswith("# public key: ")
        )
        recipient = recipient_line.split(": ", 1)[1]
        encrypted_path = tmp_path / "recovery-backup.dump.age"

        report = run_recovery_check(
            source_dsn,
            restore_dsn,
            encrypted_backup_path=encrypted_path,
            age_recipient=recipient,
        )

        assert report["status"] == "passed"
        assert report["source_modified"] is False
        assert report["source_schema_version"] == CURRENT_SCHEMA_VERSION
        assert report["restored_schema_version_before_migration"] == CURRENT_SCHEMA_VERSION
        assert report["upgraded_schema_version"] == CURRENT_SCHEMA_VERSION
        assert report["migration_applied"] is False
        assert report["encrypted_backup"]["encryption"] == "age"
        assert report["encrypted_backup"]["size_bytes"] == encrypted_path.stat().st_size
        assert len(report["encrypted_backup"]["sha256"]) == 64

        # Only the age-encrypted file is retained. Verify it decrypts to a valid
        # PostgreSQL custom-format dump using the temporary private key.
        decrypted_path = tmp_path / "recovery-backup.dump"
        subprocess.run(
            [
                age,
                "--decrypt",
                "--identity",
                str(key_path),
                "--output",
                str(decrypted_path),
                str(encrypted_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert decrypted_path.read_bytes().startswith(b"PGDMP")
        with psycopg.connect(restore_dsn) as restored:
            assert restored.execute(
                "SELECT max(version) FROM ofi_schema_migrations"
            ).fetchone()[0] == CURRENT_SCHEMA_VERSION
    finally:
        drop_database(admin_dsn, restore_name)
        drop_database(admin_dsn, source_name)
