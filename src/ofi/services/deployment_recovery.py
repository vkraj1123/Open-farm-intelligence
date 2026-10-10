"""Concrete PostgreSQL + HTTPS composition root for scheduled recovery.

This factory wires existing production-oriented adapters. It does not create
schema, provision credentials, or claim that a provider endpoint is real; those
remain deployment responsibilities.
"""

from __future__ import annotations

import os
import re
from typing import Mapping

from ofi.services.dispatch_recovery_worker import DispatchRecoveryWorker
from ofi.services.reconciliation_adapter import (
    HttpsReconciliationTransport,
    ProviderReconciliationAdapter,
)
from ofi.services.transaction_repository import PostgresTransactionRepository


_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_REQUIRED_TABLES = {
    "service_transactions",
    "service_transaction_events",
    "service_execution_attempts",
    "service_reconciliation_events",
}


def _validate_database(psycopg_module, database_url: str) -> None:
    """Fail startup if PostgreSQL is unreachable or the recovery schema is absent."""
    with psycopg_module.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() "
                "AND table_name IN (%s, %s, %s, %s)",
                tuple(sorted(_REQUIRED_TABLES)),
            )
            existing = {row[0] for row in cursor.fetchall()}
    missing = _REQUIRED_TABLES - existing
    if missing:
        raise RuntimeError(
            "recovery database schema is incomplete; missing required tables: "
            + ", ".join(sorted(missing))
        )


def _required(name: str, environ: Mapping[str, str]) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ValueError(f"required deployment setting is missing: {name}")
    return value


def _provider_env_prefix(provider_id: str) -> str:
    if not _PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("provider IDs may contain only letters, digits, '.', '_' and '-'")
    return "OFI_RECOVERY_PROVIDER_" + re.sub(r"[^A-Za-z0-9]", "_", provider_id).upper()


def build_worker(*, environ: Mapping[str, str] | None = None) -> DispatchRecoveryWorker:
    """Build the recovery worker from deployment environment.

    Required variables:
      - OFI_DATABASE_URL
      - OFI_RECOVERY_PROVIDER_IDS (comma-separated provider IDs)
      - OFI_RECOVERY_PROVIDER_<NORMALIZED_ID>_ENDPOINT
      - OFI_RECOVERY_PROVIDER_<NORMALIZED_ID>_SECRET

    Optional per-provider timeout:
      - OFI_RECOVERY_PROVIDER_<NORMALIZED_ID>_TIMEOUT_SECONDS (default 5)

    The PostgreSQL schema must already be migrated. Each endpoint must implement
    OFI's signed reconciliation response contract; this factory cannot establish
    provider authority or provider-side idempotency on its own.
    """
    env = os.environ if environ is None else environ
    database_url = _required("OFI_DATABASE_URL", env)
    provider_list = _required("OFI_RECOVERY_PROVIDER_IDS", env)
    provider_ids = [item.strip() for item in provider_list.split(",") if item.strip()]
    if not provider_ids:
        raise ValueError("at least one reconciliation provider must be configured")
    if len(set(provider_ids)) != len(provider_ids):
        raise ValueError("reconciliation provider IDs must be unique")

    configured: dict[str, tuple[str, str, float]] = {}
    prefixes: set[str] = set()
    for provider_id in provider_ids:
        prefix = _provider_env_prefix(provider_id)
        if prefix in prefixes:
            raise ValueError("provider IDs collide after environment-name normalization")
        prefixes.add(prefix)
        endpoint = _required(f"{prefix}_ENDPOINT", env)
        secret = _required(f"{prefix}_SECRET", env)
        raw_timeout = env.get(f"{prefix}_TIMEOUT_SECONDS", "5").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("provider timeout must be a positive number") from exc
        if timeout <= 0:
            raise ValueError("provider timeout must be a positive number")
        configured[provider_id] = (endpoint, secret, timeout)

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL recovery requires the optional 'postgres' dependency"
        ) from exc

    _validate_database(psycopg, database_url)
    repository = PostgresTransactionRepository(
        connection_factory=lambda: psycopg.connect(database_url)
    )
    adapters = {
        provider_id: ProviderReconciliationAdapter(
            provider_id=provider_id,
            provider_secret=secret,
            transport=HttpsReconciliationTransport(
                endpoint,
                timeout_seconds=timeout,
            ),
        )
        for provider_id, (endpoint, secret, timeout) in configured.items()
    }
    return DispatchRecoveryWorker(repository=repository, adapters=adapters)


if __name__ == "__main__":
    raise SystemExit(
        "Use build_worker() from the ofi-recovery runner; do not execute this module directly."
    )
