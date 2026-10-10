from datetime import datetime, timezone

import pytest

from ofi.services.reconciliation import ReconciliationEvidence
from ofi.services.service_transaction import ExecutionAttempt, ServiceTransaction
from ofi.services.transaction_repository import (
    InMemoryTransactionRepository,
    TransactionConflictError,
)


def setup_repo():
    repo = InMemoryTransactionRepository()
    tx = ServiceTransaction(
        transaction_id="txn-1",
        idempotency_key="idempotency-1",
        request_fingerprint="fingerprint-1",
        action_id="action-1",
        provider_id="provider-1",
    )
    repo.create(tx)
    now = datetime.now(timezone.utc)
    repo.create_attempt(
        ExecutionAttempt(
            attempt_id="attempt-1",
            transaction_id="txn-1",
            attempt_number=1,
            provider_id="provider-1",
            status="unknown",
            created_at=now,
            updated_at=now,
        )
    )
    return repo


def evidence(**overrides):
    values = {
        "provider_id": "provider-1",
        "transaction_id": "txn-1",
        "attempt_id": "attempt-1",
        "event_id": "reconcile-event-1",
        "status": "not_executed",
        "checked_at": datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
        "external_reference": None,
        "message": "No execution found.",
    }
    values.update(overrides)
    return ReconciliationEvidence(**values)


def test_first_reconciliation_receipt_is_recorded_and_identical_replay_is_noop():
    repo = setup_repo()
    item = evidence()
    assert repo.record_reconciliation_evidence(item, payload_sha256="a" * 64) is True
    assert repo.record_reconciliation_evidence(item, payload_sha256="a" * 64) is False


@pytest.mark.parametrize(
    ("item", "digest"),
    [
        (evidence(attempt_id="attempt-other"), "a" * 64),
        (evidence(status="executed"), "a" * 64),
        (evidence(), "b" * 64),
    ],
)
def test_event_id_reuse_with_different_evidence_is_rejected(item, digest):
    repo = setup_repo()
    repo.record_reconciliation_evidence(evidence(), payload_sha256="a" * 64)
    with pytest.raises(TransactionConflictError, match="different evidence"):
        repo.record_reconciliation_evidence(item, payload_sha256=digest)


def test_reconciliation_receipt_requires_correct_provider_and_attempt_ownership():
    repo = setup_repo()
    with pytest.raises(TransactionConflictError, match="does not own"):
        repo.record_reconciliation_evidence(
            evidence(provider_id="provider-other"), payload_sha256="a" * 64
        )


def test_reconciliation_receipt_requires_sha256_length():
    repo = setup_repo()
    with pytest.raises(ValueError, match="SHA-256"):
        repo.record_reconciliation_evidence(evidence(), payload_sha256="short")
