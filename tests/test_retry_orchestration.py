from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from ofi.services.reconciliation import ReconciliationEvidence
from ofi.services.reconciliation_recovery import ReconciliationRecoveryService
from ofi.services.retry_orchestrator import GuardedRetryOrchestrator
from ofi.services.service_transaction import ExecutionAttempt, ServiceTransaction
from ofi.services.transaction_repository import (
    InMemoryTransactionRepository,
    TransactionConflictError,
)


NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


class Adapter:
    def __init__(self, evidence):
        self.evidence = evidence

    def reconcile_with_digest(self, *, transaction_id, attempt_id, now=None):
        return self.evidence, "a" * 64


def setup(status="unknown"):
    repo = InMemoryTransactionRepository()
    repo.create(ServiceTransaction(
        transaction_id="txn-retry",
        idempotency_key="key-retry",
        request_fingerprint="fp-retry",
        action_id="action-retry",
        provider_id="provider-retry",
        status="submitted",
    ))
    repo.create_attempt(ExecutionAttempt(
        attempt_id="attempt-1",
        transaction_id="txn-retry",
        attempt_number=1,
        provider_id="provider-retry",
        status=status,
        created_at=NOW,
        updated_at=NOW,
    ))
    return repo


def reconcile_not_executed(repo):
    evidence = ReconciliationEvidence(
        provider_id="provider-retry",
        transaction_id="txn-retry",
        attempt_id="attempt-1",
        event_id="event-not-executed",
        status="not_executed",
        checked_at=NOW,
        message="Provider confirms no execution.",
    )
    return ReconciliationRecoveryService(
        adapter=Adapter(evidence), repository=repo
    ).reconcile_attempt(
        transaction_id="txn-retry", attempt_id="attempt-1", now=NOW
    )


def test_retry_requires_applied_verified_non_execution_evidence():
    repo = setup()
    retry = GuardedRetryOrchestrator(repo)
    with pytest.raises(TransactionConflictError, match="applied provider evidence"):
        retry.prepare_retry(
            transaction_id="txn-retry",
            prior_attempt_id="attempt-1",
            retry_request_key="retry-key-1",
            attempt_id="attempt-2",
        )
    assert len(repo.list_attempts("txn-retry")) == 1


def test_retry_creates_ready_attempt_with_atomic_monotonic_number():
    repo = setup()
    reconcile_not_executed(repo)
    attempt = GuardedRetryOrchestrator(repo).prepare_retry(
        transaction_id="txn-retry",
        prior_attempt_id="attempt-1",
        retry_request_key="retry-key-1",
        attempt_id="attempt-2",
    )
    assert attempt.attempt_number == 2
    assert attempt.status == "ready"
    assert attempt.retry_of_attempt_id == "attempt-1"
    assert attempt.retry_request_key == "retry-key-1"
    assert [a.attempt_number for a in repo.list_attempts("txn-retry")] == [1, 2]


def test_retry_request_replay_returns_same_attempt_and_conflicting_binding_fails():
    repo = setup()
    reconcile_not_executed(repo)
    retry = GuardedRetryOrchestrator(repo)
    args = dict(
        transaction_id="txn-retry",
        prior_attempt_id="attempt-1",
        retry_request_key="retry-key-1",
        attempt_id="attempt-2",
    )
    first = retry.prepare_retry(**args)
    replay = retry.prepare_retry(**{**args, "attempt_id": "ignored-on-replay"})
    assert first.attempt_id == replay.attempt_id == "attempt-2"
    assert len(repo.list_attempts("txn-retry")) == 2
    with pytest.raises(TransactionConflictError, match="different retry"):
        retry.prepare_retry(**{**args, "prior_attempt_id": "another-attempt"})


def test_concurrent_duplicate_retry_requests_create_only_one_attempt():
    repo = setup()
    reconcile_not_executed(repo)
    retry = GuardedRetryOrchestrator(repo)
    def prepare(_):
        return retry.prepare_retry(
            transaction_id="txn-retry",
            prior_attempt_id="attempt-1",
            retry_request_key="same-retry-key",
            attempt_id="attempt-2",
        )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(prepare, range(8)))
    assert {item.attempt_id for item in results} == {"attempt-2"}
    assert len(repo.list_attempts("txn-retry")) == 2


@pytest.mark.parametrize("status", ["executed", "pending", "unknown"])
def test_non_nonexecution_evidence_never_authorizes_retry(status):
    repo = setup()
    evidence = ReconciliationEvidence(
        provider_id="provider-retry",
        transaction_id="txn-retry",
        attempt_id="attempt-1",
        event_id=f"event-{status}",
        status=status,
        checked_at=NOW,
    )
    ReconciliationRecoveryService(
        adapter=Adapter(evidence), repository=repo
    ).reconcile_attempt(transaction_id="txn-retry", attempt_id="attempt-1", now=NOW)
    with pytest.raises(TransactionConflictError):
        GuardedRetryOrchestrator(repo).prepare_retry(
            transaction_id="txn-retry",
            prior_attempt_id="attempt-1",
            retry_request_key="retry-key-1",
            attempt_id="attempt-2",
        )
    assert len(repo.list_attempts("txn-retry")) == 1
