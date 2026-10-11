"""Apply ordered PostgreSQL/PostGIS schema migrations."""

from __future__ import annotations

import argparse
import os
import sys

from ofi.twin.migration_registry import CURRENT_SCHEMA_VERSION, MIGRATIONS


def apply_schema(database_url: str | None = None) -> int:
    """Apply pending migrations atomically and return the final schema version.

    The canonical schema.sql file remains available for local/test baseline
    provisioning. Application upgrades should use this ordered migration runner.
    """
    dsn = database_url or os.getenv("OFI_DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Set OFI_DATABASE_URL or pass --database-url to apply OFI migrations"
        )

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required; install the PostgreSQL extra with "
            "pip install '.[postgres]'"
        ) from exc

    known_versions = {migration.version for migration in MIGRATIONS}
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            # Serialize concurrent startup/deployment migration attempts.
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", (5198921, 1))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ofi_schema_migrations (
                    version INTEGER PRIMARY KEY CHECK (version >= 1),
                    name TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    checksum TEXT
                )
                """
            )
            # Compatibility for databases stamped by the previous v1 bootstrap,
            # whose marker table predates checksum tracking.
            cursor.execute(
                "ALTER TABLE ofi_schema_migrations "
                "ADD COLUMN IF NOT EXISTS checksum TEXT"
            )
            cursor.execute(
                "SELECT version, name, checksum "
                "FROM ofi_schema_migrations ORDER BY version"
            )
            rows = cursor.fetchall()

        applied = {
            int(version): (name, checksum)
            for version, name, checksum in rows
        }
        if applied and max(applied) > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {max(applied)} is newer than this "
                f"application supports ({CURRENT_SCHEMA_VERSION}); upgrade the "
                "application before applying schema changes"
            )

        unknown_versions = sorted(set(applied) - known_versions)
        if unknown_versions:
            raise RuntimeError(
                f"Database contains unsupported schema migration versions: "
                f"{unknown_versions}"
            )

        for migration in MIGRATIONS:
            checksum = migration.checksum
            existing_record = applied.get(migration.version)

            if existing_record is not None:
                existing_name, existing_checksum = existing_record
                if existing_name != migration.name:
                    raise RuntimeError(
                        f"Migration version {migration.version} is recorded as "
                        f"{existing_name!r}, expected {migration.name!r}"
                    )
                if existing_checksum is not None and existing_checksum != checksum:
                    raise RuntimeError(
                        f"Checksum mismatch for applied migration "
                        f"{migration.version} ({migration.name}); applied SQL "
                        "must not be edited"
                    )
                if existing_checksum is None:
                    # Only v1 existed before checksum tracking was introduced.
                    # Do not silently bless checksum-less records for new SQL.
                    if migration.version != 1:
                        raise RuntimeError(
                            f"Applied migration {migration.version} is missing "
                            "its checksum; inspect the migration ledger before proceeding"
                        )
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "UPDATE ofi_schema_migrations SET checksum=%s "
                            "WHERE version=%s AND checksum IS NULL",
                            (checksum, migration.version),
                        )
                    applied[migration.version] = (migration.name, checksum)
                continue

            later_applied = sorted(version for version in applied if version > migration.version)
            if later_applied:
                raise RuntimeError(
                    f"Cannot apply missing migration {migration.version}; "
                    f"later migration(s) already exist: {later_applied}"
                )
            missing_predecessors = [
                earlier.version
                for earlier in MIGRATIONS
                if earlier.version < migration.version
                and earlier.version not in applied
            ]
            if missing_predecessors:
                raise RuntimeError(
                    f"Cannot apply migration {migration.version}; predecessor "
                    f"migration(s) are missing: {missing_predecessors}"
                )

            sql = migration.sql
            if not sql.strip():
                raise RuntimeError(
                    f"Migration {migration.version} has an empty SQL file"
                )
            connection.execute(sql)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ofi_schema_migrations (version, name, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.name, checksum),
                )
            applied[migration.version] = (migration.name, checksum)

        final_version = max(applied, default=0)
        expected_versions = set(known_versions)
        if set(applied) != expected_versions or final_version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                "Migration run ended with an incomplete schema history; "
                f"expected versions {sorted(expected_versions)}, "
                f"found {sorted(applied)}"
            )

    return final_version


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply ordered Open Farm Intelligence PostgreSQL/PostGIS migrations."
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
        # Driver errors sometimes include the full connection string.
        # Keep terminal output safe; protected deployment logs can carry details.
        print(
            f"OFI schema migration failed ({type(exc).__name__}); "
            "inspect secure deployment logs for details.",
            file=sys.stderr,
        )
        return 1
    print(f"OFI schema is at version {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
