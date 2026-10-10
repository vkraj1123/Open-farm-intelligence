from datetime import datetime, timedelta, timezone

from ofi.services.dispatch_recovery_worker import DispatchRecoveryWorker
from ofi.services.reconciliation import ReconciliationEvidence
from ofi.services.service_transaction import ExecutionAttempt, ServiceTransaction
from ofi.services.transaction_repository import InMemoryTransactionRepository


class FakeAdapter:
    def __init__(self, status="not_executed", fail=False):
        self.status = status
        self.fail = fail
        self.calls = 0

    def reconcile_with_digest(self, *, transaction_id, attempt_id, now=None):
        self.calls += 1
        if self.fail:
            raise TimeoutError("reconciliation endpoint timed out")
        return ReconciliationEvidence(
            provider_id="provider-1",
            transaction_id=transaction_id,
            attempt_id=attempt_id,
            event_id=f"reconcile-{self.calls}",
            status=self.status,
            checked_at=now or datetime.now(timezone.utc),
            message=f"provider says {self.status}",
        ), f"{self.calls:064x}"


def prepared_repo():
    repo = InMemoryTransactionRepository()
    repo.create(ServiceTransaction(
        transaction_id="txn-worker",
        idempotency_key="worker-key",
        request_fingerprint="worker-fingerprint",
        action_id="worker-action",
        provider_id="provider-1",
        status="submitted",
    ))
    now = datetime.now(timezone.utc)
    repo.create_attempt(ExecutionAttempt(
        attempt_id="attempt-worker",
        transaction_id="txn-worker",
        attempt_number=1,
        provider_id="provider-1",
        status="ready",
        created_at=now,
        updated_at=now,
    ))
    repo.claim_attempt_for_dispatch("attempt-worker")
    return repo, now


def test_worker_reconciles_stale_dispatch_and_never_submits_retry():
    repo, now = prepared_repo()
    adapter = FakeAdapter("not_executed")
    worker = DispatchRecoveryWorker(repository=repo, adapters={"provider-1": adapter})
    report = worker.run_once(now=now + timedelta(hours=1), stale_after=timedelta(minutes=5))

    assert report.candidates == 1
    assert report.reconciled == 1
    assert report.errors == 0
    assert report.items[0].result.retry_assessment.decision == "retry_allowed"
    attempts = repo.list_attempts("txn-worker")
    assert len(attempts) == 1
    assert attempts[0].status == "failed"
    assert report.metrics == {
        "candidates": 1,
        "reconciled": 1,
        "errors": 0,
        "confirmed_executed": 0,
        "confirmed_not_executed": 1,
        "pending": 0,
        "unknown": 0,
    }


def test_worker_keeps_ambiguous_provider_response_unknown_for_later_recheck():
    repo, now = prepared_repo()
    adapter = FakeAdapter("unknown")
    worker = DispatchRecoveryWorker(repository=repo, adapters={"provider-1": adapter})
    first = worker.run_once(now=now + timedelta(hours=1), stale_after=timedelta(minutes=5))
    assert first.reconciled == 1
    assert repo.list_attempts("txn-worker")[0].status == "unknown"
    assert first.metrics["unknown"] == 1
    assert first.metrics["pending"] == 0

    second = worker.run_once(now=now + timedelta(hours=2), stale_after=timedelta(minutes=5))
    assert second.candidates == 1
    assert second.reconciled == 1
    assert adapter.calls == 2
    assert repo.list_attempts("txn-worker")[0].status == "unknown"


def test_worker_missing_adapter_leaves_retryable_unknown_candidate():
    repo, now = prepared_repo()
    worker = DispatchRecoveryWorker(repository=repo, adapters={})
    first = worker.run_once(now=now + timedelta(hours=1), stale_after=timedelta(minutes=5))
    assert first.errors == 1
    assert repo.list_attempts("txn-worker")[0].status == "unknown"

    second = worker.run_once(now=now + timedelta(hours=2), stale_after=timedelta(minutes=5))
    assert second.candidates == 1
    assert second.errors == 1


def test_worker_reconciliation_failure_is_unknown_and_retried_later():
    repo, now = prepared_repo()
    adapter = FakeAdapter(fail=True)
    worker = DispatchRecoveryWorker(repository=repo, adapters={"provider-1": adapter})
    first = worker.run_once(now=now + timedelta(hours=1), stale_after=timedelta(minutes=5))
    assert first.errors == 1
    assert repo.list_attempts("txn-worker")[0].status == "unknown"

    second = worker.run_once(now=now + timedelta(hours=2), stale_after=timedelta(minutes=5))
    assert second.candidates == 1
    assert second.errors == 1
    assert adapter.calls == 2
