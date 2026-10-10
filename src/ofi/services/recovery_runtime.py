"""One-call runtime orchestration for scheduled dispatch recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from ofi.services.dispatch_recovery_worker import (
    DispatchRecoveryReport,
    DispatchRecoveryWorker,
)
from ofi.services.recovery_health import (
    RecoveryHealthAssessment,
    RecoveryHealthThresholds,
    assess_recovery_health,
)
from ofi.services.recovery_observability import log_recovery_health


@dataclass(frozen=True)
class RecoveryCycleResult:
    """Outputs from one worker run and its operational health assessment."""

    report: DispatchRecoveryReport
    assessment: RecoveryHealthAssessment
    event: dict[str, Any]


def run_recovery_cycle(
    worker: DispatchRecoveryWorker,
    *,
    thresholds: RecoveryHealthThresholds,
    logger: logging.Logger,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=5),
    limit: int = 100,
) -> RecoveryCycleResult:
    """Run recovery once, assess health, and emit one structured event.

    This function is suitable as a deployment scheduler's callable entry point.
    It does not configure a scheduler, mutate recovery policy, send alerts to an
    external receiver, create retries, or dispatch new attempts. A failed worker
    run raises to the caller; partial worker failures are represented in its
    report and assessed normally.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    report = worker.run_once(
        now=moment,
        stale_after=stale_after,
        limit=limit,
    )
    assessment = assess_recovery_health(report, thresholds=thresholds)
    event = log_recovery_health(
        assessment,
        logger=logger,
        observed_at=moment,
    )
    return RecoveryCycleResult(
        report=report,
        assessment=assessment,
        event=event,
    )
