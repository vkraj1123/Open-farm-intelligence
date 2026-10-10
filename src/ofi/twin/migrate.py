"""Apply the versioned, idempotent PostgreSQL/PostGIS schema."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ofi.services.database_readiness import CURRENT_SCHEMA_VERSION

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def apply_schema(database_url: str | None = None) -> int:
    """Apply schema.sql atomically and return its recorded version."""
    dsn = database_url or os.getenv("OFI_DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Set OFI_DATABASE_URL or pass --database-url to apply the OFI schema"
        )

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required; install the PostgreSQL extra with "
            "pip install '.[postgres]'"
        ) from exc

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.ofi_schema_migrations')")
            marker_table = cursor.fetchone()[0]
            if marker_table is not None:
                cursor.execute("SELECT max(version) FROM ofi_schema_migrations")
                row = cursor.fetchone()
                current_version = row[0] if row else None
                if current_version is not None and current_version > CURRENT_SCHEMA_VERSION:
                    raise RuntimeError(
                        f"Database schema version {current_version} is newer than "
                        f"this application supports ({CURRENT_SCHEMA_VERSION}); "
                        "upgrade the application before applying schema changes"
                    )

        # Psycopg executes the complete idempotent SQL script in the same
        # connection transaction; the marker is written only at its end.
        connection.execute(schema_sql)
        with connection.cursor() as cursor:
            cursor.execute("SELECT max(version) FROM ofi_schema_migrations")
            row = cursor.fetchone()
            applied_version = row[0] if row else None
        if applied_version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                "Schema script completed without recording the expected version "
                f"{CURRENT_SCHEMA_VERSION}"
            )
    return applied_version


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply the Open Farm Intelligence PostgreSQL/PostGIS schema."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("OFI_DATABASE_URL"),
        help="Database DSN (defaults to OFI_DATABASE_URL; never printed)",
    )
    args = parser.parse_args()
    try:
        version = apply_schema(args.database_url)
    except Exception as exc:
        print(f"OFI schema migration failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    print(f"OFI schema is at version {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
