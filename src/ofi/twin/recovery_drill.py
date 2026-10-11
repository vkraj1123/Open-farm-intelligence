"""Orchestrate a disposable-target recovery drill for scheduled automation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from uuid import uuid4
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from ofi.twin.recovery_check import _database_identity, run_recovery_check


def run_recovery_drill(
    source_dsn: str,
    restore_admin_dsn: str,
    *,
    encrypted_backup_path: str,
    age_recipient: str,
) -> dict[str, Any]:
    """Create a unique empty restore DB, rehearse it, and always remove it.

    The restore-admin role must be permitted to create and drop databases.
    The database name is generated for each run so a scheduled drill never
    reuses a populated target. The retained artifact is age-encrypted only;
    the temporary plaintext pg_dump is managed and deleted by recovery_check.
    """
    admin_info = conninfo_to_dict(restore_admin_dsn)
    if not admin_info.get("dbname"):
        raise ValueError("Restore-admin DSN must explicitly identify a maintenance database")

    target_name = f"ofi_recovery_{uuid4().hex[:20]}"
    target_dsn = make_conninfo(restore_admin_dsn, dbname=target_name)
    if _database_identity(source_dsn) == _database_identity(target_dsn):
        raise ValueError("Source and generated restore database must be different")

    with psycopg.connect(restore_admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_name))
        )

    try:
        report = run_recovery_check(
            source_dsn,
            target_dsn,
            encrypted_backup_path=encrypted_backup_path,
            age_recipient=age_recipient,
        )
    finally:
        with psycopg.connect(restore_admin_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(target_name))
            )

    report["restore_database_disposed"] = True
    report["restore_database_name"] = target_name
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a staging backup/restore check using a unique disposable database "
            "and retain only an age-encrypted backup artifact."
        )
    )
    parser.add_argument(
        "--source-dsn",
        default=os.getenv("OFI_RECOVERY_SOURCE_DSN"),
        help="Read-only staging database DSN (defaults to OFI_RECOVERY_SOURCE_DSN)",
    )
    parser.add_argument(
        "--restore-admin-dsn",
        default=os.getenv("OFI_RECOVERY_RESTORE_ADMIN_DSN"),
        help="DSN with CREATEDB/DROP DATABASE rights (defaults to OFI_RECOVERY_RESTORE_ADMIN_DSN)",
    )
    parser.add_argument(
        "--encrypted-backup-path",
        required=True,
        help="Destination path for the age-encrypted backup",
    )
    parser.add_argument(
        "--age-recipient",
        default=os.getenv("OFI_BACKUP_AGE_RECIPIENT"),
        help="age public recipient (defaults to OFI_BACKUP_AGE_RECIPIENT)",
    )
    args = parser.parse_args()
    if not args.source_dsn or not args.restore_admin_dsn or not args.age_recipient:
        print(
            "Set OFI_RECOVERY_SOURCE_DSN, OFI_RECOVERY_RESTORE_ADMIN_DSN, "
            "and OFI_BACKUP_AGE_RECIPIENT.",
            file=sys.stderr,
        )
        return 2
    try:
        report = run_recovery_drill(
            args.source_dsn,
            args.restore_admin_dsn,
            encrypted_backup_path=args.encrypted_backup_path,
            age_recipient=args.age_recipient,
        )
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, (RuntimeError, ValueError))
            else "Database or backup operation failed; sensitive details withheld."
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
