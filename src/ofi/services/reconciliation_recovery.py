"""Apply authenticated reconciliation evidence to durable execution state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ofi.services.reconciliation import ReconciliationEvidence
from ofi.services.retry_policy import RetryAssessment, assess_retry
from ofi.services.service_transaction import ExecutionAttempt, ServiceTransaction
from ofi.services.transaction_repository import TransactionRepository


class ReconciliationAdapter(Protocol):
    def reconcile_with_digest(
        self, *, transaction_id: str, attempt_id: str, now: datetime | None = None
    ) -> tuple[ReconciliationEvidence, str]:
        ...


@dataclass(frozen=True)
class RecoveryResult:
    evidence: ReconciliationEvidence
    applied: bool
    transaction: ServiceTransaction
    attempt: ExecutionAttempt
    retry_assessment: RetryAssessment


class ReconciliationRecoveryService:
    """Fetch, verify, persist and apply evidence without creating retry attempts.

    The repository applies the receipt and lifecycle changes atomically. This
    service only assesses retry eligibility; a separate, guarded orchestrator
    must create any subsequent attempt.
    """

    def __init__(
        self,
        *,
        adapter: ReconciliationAdapter,
        repository: TransactionRepository,
    ) -> None:
        self._adapter = adapter
        self._repository = repository

    def reconcile_attempt(
        self,
        *,
        transaction_id: str,
        attempt_id: str,
        now: datetime | None = None,
    ) -> RecoveryResult:
        evidence, payload_sha256 = self._adapter.reconcile_with_digest(
            transaction_id=transaction_id,
            attempt_id=attempt_id,
            now=now,
        )
        applied = self._repository.apply_reconciliation_evidence(
            evidence, payload_sha256=payload_sha256
        )
        transaction = self._repository.get(transaction_id)
        attempts = self._repository.list_attempts(transaction_id)
        attempt = next((item for item in attempts if item.attempt_id == attempt_id), None)
        if attempt is None:
            raise KeyError(attempt_id)

        if evidence.status == "executed":
            assessment = assess_retry(
                attempt_status=attempt.status,
                reconciled=True,
                execution_confirmed=True,
            )
        elif evidence.status == "not_executed":
            assessment = assess_retry(
                attempt_status=attempt.status,
                reconciled=True,
                execution_confirmed=False,
            )
        else:
            assessment = assess_retry(
                attempt_status=attempt.status,
                reconciled=False,
                execution_confirmed=None,
            )

        return RecoveryResult(
            evidence=evidence,
            applied=applied,
            transaction=transaction,
            attempt=attempt,
            retry_assessment=assessment,
        )
