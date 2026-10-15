"""Ordered PostgreSQL schema migration registry.

Applied migration files are immutable: add a new numbered SQL file and registry
entry instead of editing an already-applied migration. The SHA-256 checksum is
recorded by the runner to detect accidental edits after deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


MIGRATION_DIRECTORY = Path(__file__).with_name("migrations")


@dataclass(frozen=True)
class SchemaMigration:
    version: int
    name: str
    filename: str

    @property
    def path(self) -> Path:
        return MIGRATION_DIRECTORY / self.filename

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return sha256(self.sql.encode("utf-8")).hexdigest()


MIGRATIONS = (
    SchemaMigration(
        version=1,
        name="initial_postgis_case_service_and_evidence_schema",
        filename="001_initial_postgis_case_service_and_evidence_schema.sql",
    ),
    SchemaMigration(
        version=2,
        name="case_record_version_nonnegative_constraint",
        filename="002_case_record_version_constraint.sql",
    ),
    SchemaMigration(
        version=3,
        name="consent_scopes_and_revocation_ledger",
        filename="003_consent_scopes_and_revocation.sql",
    ),
    SchemaMigration(
        version=4,
        name="tenant_scoped_data_boundaries",
        filename="004_tenant_scoped_data_boundaries.sql",
    ),
    SchemaMigration(
        version=5,
        name="tenant_membership_registry_and_audit",
        filename="005_tenant_membership_registry_and_audit.sql",
    ),
    SchemaMigration(
        version=6,
        name="tenant_invitation_lifecycle",
        filename="006_tenant_invitation_lifecycle.sql",
    ),
)

versions = tuple(item.version for item in MIGRATIONS)
if not versions or versions != tuple(range(1, len(versions) + 1)):
    raise RuntimeError("schema migration versions must be contiguous, ordered, and start at 1")
if len({item.name for item in MIGRATIONS}) != len(MIGRATIONS):
    raise RuntimeError("schema migration names must be unique")
if len({item.filename for item in MIGRATIONS}) != len(MIGRATIONS):
    raise RuntimeError("schema migration filenames must be unique")

CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version
