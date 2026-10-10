from datetime import datetime, timezone

import pytest

from ofi.services.reconciliation import ReconciliationEvidence
from ofi.services.reconciliation_recovery import ReconciliationRecoveryService
from ofi.services.service_transaction import ExecutionAttempt, ServiceTransaction
from ofi.services.transaction_repository import (
    InMemoryTransactionRepository,
    TransactionConflictError,
)


NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


class FakeAdapter:
    def __init__(self, item, digest="a" * 64):
        self.item = item
        self.digest = digest
        self.calls = []

    def reconcile_with_digest(self, *, transaction_id, attempt_id, now=None):
        self.calls.append((transaction_id, attempt_id))
        return self.item, self.digest


def setup():
    repo = InMemoryTransactionRepository()
    repo.create(ServiceTransaction(
        transaction_id="txn-1",
        idempotency_key="key-1",
        request_fingerprint="fp-1",
        action_id="action-1",
        provider_id="provider-1",
        status="submitted",
    ))
    repo.create_attempt(ExecutionAttempt(
        attempt_id="attempt-1",
        transaction_id="txn-1",
        attempt_number=1,
        provider_id="provider-1",
        status="unknown",
        created_at=NOW,
        updated_at=NOW,
    ))
    return repo


def evidence(status):
    return ReconciliationEvidence(
        provider_id="provider-1",
        transaction_id="txn-1",
        attempt_id="attempt-1",
        event_id=f"event-{status}",
        status=status,
        checked_at=NOW,
        message=f"Provider says {status}.",
    )


def service(repo, item):
    adapter = FakeAdapter(item)
    return ReconciliationRecoveryService(adapter=adapter, repository=repo), adapter


def test_verified_non_execution_is_applied_and_only_assesses_retry():
    repo = setup()
    recovery, adapter = service(repo, evidence("not_executed"))
    result = recovery.reconcile_attempt(
        transaction_id="txn-1", attempt_id="attempt-1", now=NOW
    )
    assert result.applied is True
    assert result.attempt.status == "failed"
    assert result.transaction.status == "submitted"
    assert result.retry_assessment.decision == "retry_allowed"
    assert len(repo.list_attempts("txn-1")) == 1
    audit = repo.list_attempt_events("attempt-1")
    assert audit[-1].event_type == "reconciliation_evidence_applied"
    assert audit[-1].from_status == "unknown"
    assert audit[-1].to_status == "failed"
    assert "not_executed" in audit[-1].detail
    assert adapter.calls == [("txn-1", "attempt-1")]


def test_confirmed_execution_advances_transaction_and_blocks_retry():
    repo = setup()
    recovery, _ = service(repo, evidence("executed"))
    result = recovery.reconcile_attempt(
        transaction_id="txn-1", attempt_id="attempt-1", now=NOW
    )
    assert result.attempt.status == "in_progress"
    assert result.transaction.status == "in_progress"
    assert result.retry_assessment.decision == "do_not_retry"


@pytest.mark.parametrize("status", ["pending", "unknown"])
def test_ambiguous_evidence_keeps_retry_blocked(status):
    repo = setup()
    recovery, _ = service(repo, evidence(status))
    result = recovery.reconcile_attempt(
        transaction_id="txn-1", attempt_id="attempt-1", now=NOW
    )
    assert result.attempt.status == "unknown"
    assert result.retry_assessment.decision == "reconcile_required"
    assert len(repo.list_attempts("txn-1")) == 1


def test_identical_reconciliation_replay_is_noop_but_returns_current_state():
    repo = setup()
    item = evidence("not_executed")
    recovery, _ = service(repo, item)
    first = recovery.reconcile_attempt(
        transaction_id="txn-1", attempt_id="attempt-1", now=NOW
    )
    second = recovery.reconcile_attempt(
        transaction_id="txn-1", attempt_id="attempt-1", now=NOW
    )
    assert first.applied is True
    assert second.applied is False
    assert second.retry_assessment.decision == "retry_allowed"
    assert len(repo.list_attempts("txn-1")) == 1
    assert len(repo.list_attempt_events("attempt-1")) == len(
        repo.list_attempt_events("attempt-1")
    )


def test_conflicting_event_replay_is_rejected():
    repo = setup()
    first = evidence("not_executed")
    recovery, _ = service(repo, first)
    recovery.reconcile_attempt(
        transaction_id="txn-1", attempt_id="attempt-1", now=NOW
    )
    conflicting = ReconciliationEvidence(
        **{**first.__dict__, "status": "executed"}
    )
    recovery, _ = service(repo, conflicting)
    with pytest.raises(TransactionConflictError, match="different evidence"):
        recovery.reconcile_attempt(
            transaction_id="txn-1", attempt_id="attempt-1", now=NOW
        )
