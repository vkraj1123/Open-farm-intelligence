"""Real-PostgreSQL integration coverage for the recovery deployment factory.

The PostgreSQL CI job sets OFI_POSTGRES_DSN and provisions an isolated database.
Local runs without that variable skip this module rather than silently using mocks.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from ofi.services.db_context import bind_connection
from ofi.services.deployment_recovery import build_worker
from ofi.services.service_transaction import ExecutionAttempt, ServiceTransaction


DATABASE_URL = os.environ.get("OFI_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the PostgreSQL service configured by CI (OFI_POSTGRES_DSN)",
)


def test_factory_and_repository_operate_against_real_postgres_schema(monkeypatch):
    import psycopg

    schema_path = Path(__file__).parents[1] / "src" / "ofi" / "twin" / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")
    # The CI database is dedicated to this test job. The schema is additive and
    # idempotent, so applying it also verifies the checked-in schema is executable.
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(schema_sql)

    provider_id = "postgres-integration"
    monkeypatch.setenv("OFI_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("OFI_RECOVERY_PROVIDER_IDS", provider_id)
    monkeypatch.setenv(
        "OFI_RECOVERY_PROVIDER_POSTGRES_INTEGRATION_ENDPOINT",
        "https://provider.example/reconcile",
    )
    monkeypatch.setenv(
        "OFI_RECOVERY_PROVIDER_POSTGRES_INTEGRATION_SECRET",
        "integration-test-secret",
    )

    transaction_id = f"it-txn-{uuid4().hex}"
    attempt_id = f"it-attempt-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    worker = build_worker()
    repository = worker._repository

    connection = psycopg.connect(DATABASE_URL)
    try:
        # Repositories share this unit-of-work connection. Rolling it back at
        # the end removes all test writes without mutating append-only audit rows.
        with bind_connection(connection):
            transaction = ServiceTransaction(
                transaction_id=transaction_id,
                idempotency_key=f"it-key-{uuid4().hex}",
                request_fingerprint="postgres-integration-fingerprint",
                action_id=f"it-action-{uuid4().hex}",
                provider_id=provider_id,
                status="submitted",
            )
            repository.create(transaction)
            repository.create_attempt(
                ExecutionAttempt(
                    attempt_id=attempt_id,
                    transaction_id=transaction_id,
                    attempt_number=1,
                    provider_id=provider_id,
                    status="ready",
                    created_at=now,
                    updated_at=now,
                )
            )

            assert repository.get(transaction_id).status == "submitted"
            attempts = repository.list_attempts(transaction_id)
            assert [(item.attempt_id, item.status) for item in attempts] == [
                (attempt_id, "ready")
            ]

            # A real worker cycle against the real repository must be able to
            # query the database and leave a fresh ready attempt untouched.
            # The placeholder endpoint is never contacted because no candidates exist.
            report = worker.run_once(
                now=now + timedelta(seconds=1),
                stale_after=timedelta(minutes=5),
            )
            assert report.candidates == 0
            assert report.reconciled == 0
            assert repository.list_attempts(transaction_id)[0].status == "ready"
    finally:
        connection.rollback()
        connection.close()

