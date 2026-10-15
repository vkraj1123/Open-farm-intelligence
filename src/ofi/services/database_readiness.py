"""Persistence readiness checks for memory and PostgreSQL API configurations."""

from __future__ import annotations

import logging
from typing import Any, Callable

from ofi.twin.migration_registry import CURRENT_SCHEMA_VERSION

logger = logging.getLogger(__name__)

# Relations exercised by the API's case/farm, evidence, and service-execution
# persistence paths. Readiness checks names only; it never reads application data.
REQUIRED_RELATIONS = (
    "farms",
    "parcels",
    "crop_cycles",
    "observations",
    "land_parties",
    "production_contracts",
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
    "ofi_tenant_memberships",
    "ofi_tenant_membership_events",
    "ofi_tenant_invitations",
    "ofi_tenant_invitation_events",
)


def check_persistence_readiness(
    connection_factory: Callable[[], Any] | None,
) -> dict[str, Any]:
    """Return a sanitized readiness report; never include DSNs or driver errors.

    Memory mode is intentionally ready for local development, but is explicitly
    marked non-durable. PostgreSQL mode is ready only when the connection works,
    PostGIS is installed, required relations exist, and the schema is current.
    """
    if connection_factory is None:
        return {
            "status": "ready",
            "persistence": "memory",
            "durable": False,
            "checks": {
                "database_connection": None,
                "postgis": None,
                "schema_version": {
                    "current": None,
                    "expected": CURRENT_SCHEMA_VERSION,
                    "ready": None,
                },
                "missing_relations": [],
            },
        }

    checks: dict[str, Any] = {
        "database_connection": False,
        "postgis": False,
        "schema_version": {
            "current": None,
            "expected": CURRENT_SCHEMA_VERSION,
            "ready": False,
        },
        "missing_relations": list(REQUIRED_RELATIONS) + ["ofi_schema_migrations"],
    }
    try:
        with connection_factory() as connection:
            checks["database_connection"] = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis')"
                )
                postgis_row = cursor.fetchone()
                checks["postgis"] = bool(postgis_row and postgis_row[0])

                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_type = 'BASE TABLE'
                    """
                )
                existing = {row[0] for row in cursor.fetchall()}
                checks["missing_relations"] = sorted(
                    (set(REQUIRED_RELATIONS) | {"ofi_schema_migrations"}) - existing
                )

                if "ofi_schema_migrations" in existing:
                    cursor.execute("SELECT max(version) FROM ofi_schema_migrations")
                    version_row = cursor.fetchone()
                    version = version_row[0] if version_row else None
                    checks["schema_version"] = {
                        "current": version,
                        "expected": CURRENT_SCHEMA_VERSION,
                        "ready": version == CURRENT_SCHEMA_VERSION,
                    }
    except Exception as exc:
        # Do not return exception text: driver exceptions can contain connection
        # parameters. The exception type is enough for safe diagnostics.
        logger.warning("Persistence readiness check failed (%s)", type(exc).__name__)
        checks["failure"] = "database_connection_or_schema_check_failed"

    version_ready = checks["schema_version"]["ready"] is True
    ready = (
        checks["database_connection"] is True
        and checks["postgis"] is True
        and not checks["missing_relations"]
        and version_ready
    )
    return {
        "status": "ready" if ready else "not_ready",
        "persistence": "postgresql",
        "durable": True,
        "checks": checks,
    }
