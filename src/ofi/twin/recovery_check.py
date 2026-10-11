"""Non-destructive PostgreSQL backup, restore, and upgrade rehearsal.

The source is never migrated or modified. The harness accepts either a source
at the prior version (to rehearse a pending migration) or the current version
(for recurring restore drills), and compares table counts/data fingerprints.
"""

from __future__ import annotations

import argparse
import hashlib
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
    "ofi_consent_grants",
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


def _table_counts(
    connection: Any, *, allow_missing_relations: bool = False
) -> dict[str, int]:
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
        if missing and not allow_missing_relations:
            raise RuntimeError(f"Database is missing required persistence tables: {missing}")
        for table in REQUIRED_RELATIONS:
            # A table introduced by the next migration is logically empty in a
            # prior-version source snapshot, so its normalized count is zero.
            if table not in existing:
                counts[table] = 0
                continue
            cursor.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            )
            counts[table] = int(cursor.fetchone()[0])
    return counts


def _data_fingerprints(
    connection: Any, *, allow_missing_relations: bool = False
) -> dict[str, str]:
    """Fingerprint every row in important data tables in deterministic order."""
    fingerprints: dict[str, str] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
            """
        )
        existing = {row[0] for row in cursor.fetchall()}
        for table in FINGERPRINT_RELATIONS:
            if table not in existing:
                if allow_missing_relations:
                    # Matches PostgreSQL md5('') for a table introduced by a
                    # migration that has not yet run on this source database.
                    fingerprints[table] = hashlib.md5(b"").hexdigest()
                    continue
                raise RuntimeError(f"Database is missing fingerprint table: {table}")
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


def _snapshot(
    dsn: str, *, allow_missing_relations: bool = False
) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        return {
            "row_counts": _table_counts(
                connection, allow_missing_relations=allow_missing_relations
            ),
            "data_fingerprints": _data_fingerprints(
                connection, allow_missing_relations=allow_missing_relations
            ),
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
        # Retain enough context to diagnose SQL/restore errors without exposing
        # credentials or full connection strings from an operational environment.
        import re

        detail = result.stderr.strip()
        for secret_key in ("password", "sslpassword"):
            secret = _connection_info(dsn).get(secret_key)
            if secret:
                detail = detail.replace(secret, "[redacted]")
        detail = re.sub(
            r"(?i)\b(password|sslpassword)=\S+",
            r"\1=[redacted]",
            detail,
        )
        detail = re.sub(
            r"(?i)(postgres(?:ql)?://)[^\s]+",
            r"\1[redacted]",
            detail,
        )
        detail = detail[:800] or "no diagnostic text was returned"
        raise RuntimeError(
            f"{tool} failed with exit code {result.returncode}: {detail}"
        )



def _encrypt_backup_with_age(
    source_path: Path,
    encrypted_path: Path,
    recipient: str,
) -> dict[str, Any]:
    """Encrypt a custom-format dump to an age recipient without retaining plaintext."""
    if encrypted_path.exists():
        raise ValueError("Encrypted backup destination already exists; choose a new path")
    executable = shutil.which("age")
    if executable is None:
        raise RuntimeError("Required encryption utility 'age' was not found; install age")
    encrypted_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                executable,
                "--recipient",
                recipient,
                "--output",
                str(encrypted_path),
                str(source_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError("Could not execute backup encryption utility 'age'") from exc
    if result.returncode:
        try:
            encrypted_path.unlink(missing_ok=True)
        finally:
            detail = result.stderr.strip()[:300] or "no diagnostic text was returned"
        raise RuntimeError(f"age backup encryption failed: {detail}")
    if not encrypted_path.is_file() or encrypted_path.stat().st_size == 0:
        encrypted_path.unlink(missing_ok=True)
        raise RuntimeError("age did not produce a non-empty encrypted backup")
    digest = hashlib.sha256()
    with encrypted_path.open("rb") as backup:
        for chunk in iter(lambda: backup.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "encryption": "age",
        "path": str(encrypted_path),
        "size_bytes": encrypted_path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def run_recovery_check(
    source_dsn: str,
    restore_dsn: str,
    *,
    encrypted_backup_path: str | Path | None = None,
    age_recipient: str | None = None,
) -> dict[str, Any]:
    """Verify a prior-version or current-version source can be safely restored.

    The source database is read-only throughout this operation. The destination
    must be a separately provisioned, empty, disposable database; it is populated
    by pg_restore and then checked/upgraded to the current schema version. The
    source may be at the immediately prior version (migration rehearsal) or the
    current version (scheduled restore drill). When requested, the custom-format
    dump is encrypted to an age public recipient before the temporary plaintext
    dump is deleted.
    """
    if (encrypted_backup_path is None) != (age_recipient is None):
        raise ValueError(
            "Encrypted backup output and age recipient must be supplied together"
        )
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
    allowed_source_versions = {expected_source_version, CURRENT_SCHEMA_VERSION}
    if source_version not in allowed_source_versions:
        versions = ", ".join(str(version) for version in sorted(allowed_source_versions))
        raise RuntimeError(
            f"Source schema version is {source_version}; this rehearsal accepts "
            f"the prior migration version or current version ({versions})"
        )

    _ensure_empty_restore_target(restore_dsn)
    source_snapshot = _snapshot(
        source_dsn, allow_missing_relations=source_version < CURRENT_SCHEMA_VERSION
    )
    encrypted_backup_report: dict[str, Any] | None = None

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

        if encrypted_backup_path is not None and age_recipient is not None:
            encrypted_backup_report = _encrypt_backup_with_age(
                backup_path, Path(encrypted_backup_path), age_recipient
            )

        _run_postgres_tool(
            "pg_restore",
            [
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                _connection_info(restore_dsn)["dbname"],
                str(backup_path),
            ],
            restore_dsn,
        )

    restored_version = _read_schema_version(restore_dsn)
    restored_snapshot = _snapshot(
        restore_dsn, allow_missing_relations=restored_version < CURRENT_SCHEMA_VERSION
    )
    if restored_version != source_version:
        raise RuntimeError(
            f"Restored database schema version is {restored_version}; "
            f"expected source version {source_version}"
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
        "migration_applied": upgraded_version > restored_version,
        "encrypted_backup": encrypted_backup_report,
        "readiness": readiness["status"],
        "row_counts": source_snapshot["row_counts"],
        "fingerprinted_relations": sorted(source_snapshot["data_fingerprints"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Back up a PostgreSQL staging database at the previous or current "
            "schema version, restore it into a separate empty test database, "
            "and verify data preservation and readiness."
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
    parser.add_argument(
        "--encrypted-backup-path",
        type=Path,
        default=os.getenv("OFI_RECOVERY_ENCRYPTED_BACKUP_PATH"),
        help="Optional path for an age-encrypted backup copy",
    )
    parser.add_argument(
        "--age-recipient",
        default=os.getenv("OFI_BACKUP_AGE_RECIPIENT"),
        help="age public recipient (defaults to OFI_BACKUP_AGE_RECIPIENT)",
    )
    args = parser.parse_args()
    if not args.source_dsn or not args.restore_dsn:
        print(
            "Set OFI_RECOVERY_SOURCE_DSN and OFI_RECOVERY_RESTORE_DSN.",
            file=sys.stderr,
        )
        return 2
    try:
        report = run_recovery_check(
            args.source_dsn,
            args.restore_dsn,
            encrypted_backup_path=args.encrypted_backup_path,
            age_recipient=args.age_recipient,
        )
    except Exception as exc:
        # Our own validation errors are useful and contain no connection string.
        # Driver exceptions can expose endpoint details, so redact their messages.
        message = (
            str(exc)
            if isinstance(exc, (RuntimeError, ValueError))
            else "Database utility or connection operation failed; details withheld."
        )
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": message,
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
