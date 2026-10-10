"""Scheduled-worker entry point for abandoned dispatch recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from ofi.services.reconciliation_recovery import (
    ReconciliationAdapter,
    ReconciliationRecoveryService,
    RecoveryResult,
)
from ofi.services.service_transaction import ExecutionAttempt
from ofi.services.transaction_repository import TransactionRepository


@dataclass(frozen=True)
class DispatchRecoveryItem:
    attempt_id: str
    provider_id: str
    status: str
    result: RecoveryResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class DispatchRecoveryReport:
    candidates: int
    items: tuple[DispatchRecoveryItem, ...]

    @property
    def reconciled(self) -> int:
        return sum(item.result is not None for item in self.items)

    @property
    def errors(self) -> int:
        return sum(item.error is not None for item in self.items)


class DispatchRecoveryWorker:
    """Recover stale dispatch claims and reconcile them with their provider.

    This class is intended to be invoked by an external scheduler. It does not
    create retry attempts or dispatch provider work. Missing adapters and
    reconciliation errors leave attempts unknown and preserve a marker so a
    later run can try again after the stale interval.
    """

    def __init__(
        self,
        *,
        repository: TransactionRepository,
        adapters: Mapping[str, ReconciliationAdapter],
    ) -> None:
        self._repository = repository
        self._adapters = dict(adapters)

    def run_once(
        self,
        *,
        now: datetime | None = None,
        stale_after: timedelta = timedelta(minutes=5),
        limit: int = 100,
    ) -> DispatchRecoveryReport:
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        if limit < 1:
            raise ValueError("limit must be positive")

        attempts = self._repository.recover_stale_dispatches(
            older_than=moment - stale_after,
            limit=limit,
        )
        items: list[DispatchRecoveryItem] = []
        for attempt in attempts:
            adapter = self._adapters.get(attempt.provider_id)
            if adapter is None:
                message = (
                    "dispatch claim exceeded recovery threshold; "
                    "reconciliation adapter unavailable"
                )
                self._mark_retryable_reconciliation(attempt, message)
                items.append(DispatchRecoveryItem(
                    attempt_id=attempt.attempt_id,
                    provider_id=attempt.provider_id,
                    status="unknown",
                    error="no reconciliation adapter registered for provider",
                ))
                continue

            try:
                result = ReconciliationRecoveryService(
                    adapter=adapter,
                    repository=self._repository,
                ).reconcile_attempt(
                    transaction_id=attempt.transaction_id,
                    attempt_id=attempt.attempt_id,
                    now=moment,
                )
                if result.evidence.status in {"pending", "unknown"}:
                    self._mark_retryable_reconciliation(
                        result.attempt,
                        "dispatch claim exceeded recovery threshold; "
                        f"provider reconciliation remains {result.evidence.status}",
                    )
                items.append(DispatchRecoveryItem(
                    attempt_id=attempt.attempt_id,
                    provider_id=attempt.provider_id,
                    status=result.attempt.status,
                    result=result,
                ))
            except Exception as exc:
                # Keep the state ambiguous and move updated_at forward so the
                # next scheduled pass respects the stale interval.
                self._mark_retryable_reconciliation(
                    attempt,
                    "dispatch claim exceeded recovery threshold; "
                    f"provider reconciliation failed: {type(exc).__name__}",
                )
                items.append(DispatchRecoveryItem(
                    attempt_id=attempt.attempt_id,
                    provider_id=attempt.provider_id,
                    status="unknown",
                    error=str(exc),
                ))

        return DispatchRecoveryReport(candidates=len(attempts), items=tuple(items))

    def _mark_retryable_reconciliation(
        self, attempt: ExecutionAttempt, message: str
    ) -> None:
        self._repository.transition_attempt(
            attempt.attempt_id,
            "unknown",
            last_error=message,
        )
