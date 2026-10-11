"""Non-destructive source / disposable-target PostgreSQL upgrade rehearsal.

The source is never migrated or modified. The harness dumps a v(N-1) source,
restores it into a separate empty database, upgrades only that restored copy,
and compares table counts and data fingerprints before and after the upgrade.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg import sql

from ofi.services.database_readiness import REQUIRED_RELATIONS, check_persistence_readiness
from ofi.twin.migrate import apply_schema
from ofi.twin.migration_registry import CURRENT_SCHEMA_VERSION, MIGRATIONS


# Data-bearing relations whose full row representations contribute to the
# preservation fingerprints. Row counts are collected for every required table.
FINGERPRINT_RELATIONS = (
    "farms",
    "parcels",
    "crop_cycles",
    "observations",
    "case_records",
    "case_events",
    "service_transactions",
    "service_transaction_events",
    "service_transaction_callbacks",
    "service_execution_attempts",
    "service_execution_attempt_events",
    "service_reconciliation_events",
    "evidence_comparison_policy_heads",
    "evidence_comparison_policy_versions",
    "evidence_comparison_assessments",
)

_PG_ENV = {
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "port": "PGPORT",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "dbname": "PGDATABASE",
    "service": "PGSERVICE",
    "passfile": "PGPASSFILE",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslcrl": "PGSSLCRL",
    "sslcrldir": "PGSSLCRLDIR",
    "application_name": "PGAPPNAME",
    "options": "PGOPTIONS",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


def _connection_info(dsn: str) -> dict[str, str]:
    info = conninfo_to_dict(dsn)
    if not info.get("dbname"):
        raise ValueError("Recovery DSNs must explicitly identify a database name")
    return info


def _tool_environment(dsn: str) -> dict[str, str]:
    info = _connection_info(dsn)
    env = os.environ.copy()
    for key, env_key in _PG_ENV.items():
        if key in info:
            env[env_key] = info[key]
    env["PGAPPNAME"] = "ofi-staging-recovery-check"
    return env


def _database_identity(dsn: str) -> tuple[str, str, str]:
    info = _connection_info(dsn)
    host = info.get("hostaddr") or info.get("host") or "localhost"
    port = info.get("port") or "5432"
    return host, port, info["dbname"]


def _read_schema_version(dsn: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('ofi_schema_migrations')")
            if cursor.fetchone()[0] is None:
                raise RuntimeError("Source/restore database has no OFI migration ledger")
            cursor.execute("SELECT max(version) FROM ofi_schema_migrations")
            row = cursor.fetchone()
    if not row or row[0] is None:
        raise RuntimeError("OFI migration ledger has no applied version")
    return int(row[0])


def _table_counts(connection: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_type = 'BASE TABLE'
            """
        )
        existing = {row[0] for row in cursor.fetchall()}
        missing = sorted(set(REQUIRED_RELATIONS) - existing)
        if missing:
            raise RuntimeError(f"Database is missing required persistence tables: {missing}")
        for table in REQUIRED_RELATIONS:
            cursor.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            )
            counts[table] = int(cursor.fetchone()[0])
    return counts


def _data_fingerprints(connection: Any) -> dict[str, str]:
    """Fingerprint every row in important data tables in deterministic order."""
    fingerprints: dict[str, str] = {}
    with connection.cursor() as cursor:
        for table in FINGERPRINT_RELATIONS:
            cursor.execute(
                sql.SQL(
                    """
                    SELECT md5(
                        COALESCE(
                            string_agg(to_jsonb(row_data)::text, E'\\n'
                                       ORDER BY to_jsonb(row_data)::text),
                            ''
                        )
                    )
                    FROM {} AS row_data
                    """
                ).format(sql.Identifier(table))
            )
            fingerprints[table] = cursor.fetchone()[0]
    return fingerprints


def _snapshot(dsn: str) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        return {
            "row_counts": _table_counts(connection),
            "data_fingerprints": _data_fingerprints(connection),
        }


def _ensure_empty_restore_target(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND namespace.nspname NOT LIKE 'pg_toast%'
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
                """
            )
            object_count = int(cursor.fetchone()[0])
    if object_count:
        raise RuntimeError(
            "Recovery target is not empty; use a separate disposable database "
            "with no user tables, views, or sequences"
        )


def _run_postgres_tool(tool: str, arguments: list[str], dsn: str) -> None:
    executable = shutil.which(tool)
    if executable is None:
        raise RuntimeError(
            f"Required PostgreSQL utility {tool!r} was not found; install "
            "the PostgreSQL client tools matching the server major version"
        )
    try:
        result = subprocess.run(
            [executable, *arguments],
            env=_tool_environment(dsn),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Could not execute PostgreSQL utility {tool!r}") from exc
    if result.returncode:
        # Never echo raw stderr or DSNs: connection utilities may include
        # operational details and deployment-specific paths.
        raise RuntimeError(
            f"{tool} failed with exit code {result.returncode}; "
            "inspect protected CI/staging logs for database utility diagnostics"
        )


def run_recovery_check(source_dsn: str, restore_dsn: str) -> dict[str, Any]:
    """Verify a source v(N-1) database can be backed up, restored and upgraded.

    The source database is read-only throughout this operation. The destination
    must be a separately provisioned, empty, disposable database; it is populated
    by pg_restore and then upgraded to the current application schema version.
    """
    source_identity = _database_identity(source_dsn)
    restore_identity = _database_identity(restore_dsn)
    if source_identity == restore_identity:
        raise ValueError("Source and restore DSNs must point to different databases")
    if source_identity[2] == restore_identity[2] and source_identity[:2] == restore_identity[:2]:
        raise ValueError("Source and restore database names must differ")

    prior_versions = {item.version for item in MIGRATIONS[:-1]}
    expected_source_version = max(prior_versions, default=0)
    if expected_source_version < 1:
        raise RuntimeError("Recovery rehearsal requires at least two registered migrations")

    source_version = _read_schema_version(source_dsn)
    if source_version != expected_source_version:
        raise RuntimeError(
            f"Source schema version is {source_version}; this rehearsal requires "
            f"version {expected_source_version} so the next migration can be tested"
        )

    _ensure_empty_restore_target(restore_dsn)
    source_snapshot = _snapshot(source_dsn)

    with tempfile.TemporaryDirectory(prefix="ofi-recovery-check-") as temporary_directory:
        backup_path = Path(temporary_directory) / "ofi-staging-backup.dump"
        _run_postgres_tool(
            "pg_dump",
            [
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                str(backup_path),
            ],
            source_dsn,
        )
        if not backup_path.is_file() or backup_path.stat().st_size == 0:
            raise RuntimeError("pg_dump did not produce a non-empty backup artifact")

        _run_postgres_tool(
            "pg_restore",
            [
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                str(backup_path),
            ],
            restore_dsn,
        )

    restored_version = _read_schema_version(restore_dsn)
    restored_snapshot = _snapshot(restore_dsn)
    if restored_version != expected_source_version:
        raise RuntimeError(
            f"Restored database schema version is {restored_version}; "
            f"expected {expected_source_version}"
        )
    if restored_snapshot != source_snapshot:
        raise RuntimeError(
            "Backup/restore verification failed: row counts or data fingerprints "
            "differ from the source snapshot"
        )

    upgraded_version = apply_schema(restore_dsn)
    upgraded_snapshot = _snapshot(restore_dsn)
    if upgraded_snapshot != source_snapshot:
        raise RuntimeError(
            "Migration verification failed: row counts or data fingerprints "
            "changed while upgrading the restored database"
        )

    readiness = check_persistence_readiness(lambda: psycopg.connect(restore_dsn))
    if readiness["status"] != "ready":
        raise RuntimeError(
            "Restored database was upgraded but did not pass the application "
            "readiness check"
        )

    # Explicitly prove that the source was not advanced by this rehearsal.
    source_version_after = _read_schema_version(source_dsn)
    if source_version_after != source_version:
        raise RuntimeError("Recovery rehearsal unexpectedly changed the source schema")

    return {
        "status": "passed",
        "source_modified": False,
        "backup_restore_verified": True,
        "row_counts_preserved": True,
        "data_fingerprints_preserved": True,
        "source_schema_version": source_version,
        "restored_schema_version_before_migration": restored_version,
        "upgraded_schema_version": upgraded_version,
        "current_schema_version": CURRENT_SCHEMA_VERSION,
        "readiness": readiness["status"],
        "row_counts": source_snapshot["row_counts"],
        "fingerprinted_relations": sorted(source_snapshot["data_fingerprints"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Back up a v(N-1) PostgreSQL staging database, restore it into a "
            "separate empty test database, and verify the upgrade preserves data."
        )
    )
    parser.add_argument(
        "--source-dsn",
        default=os.getenv("OFI_RECOVERY_SOURCE_DSN"),
        help="Read-only source/staging DSN (defaults to OFI_RECOVERY_SOURCE_DSN)",
    )
    parser.add_argument(
        "--restore-dsn",
        default=os.getenv("OFI_RECOVERY_RESTORE_DSN"),
        help="Empty disposable restore DB DSN (defaults to OFI_RECOVERY_RESTORE_DSN)",
    )
    args = parser.parse_args()
    if not args.source_dsn or not args.restore_dsn:
        print(
            "Set OFI_RECOVERY_SOURCE_DSN and OFI_RECOVERY_RESTORE_DSN.",
            file=sys.stderr,
        )
        return 2
    try:
        report = run_recovery_check(args.source_dsn, args.restore_dsn)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
