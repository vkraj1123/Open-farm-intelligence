import logging
from datetime import datetime, timedelta, timezone

import pytest

from ofi.services.dispatch_recovery_worker import (
    DispatchRecoveryItem,
    DispatchRecoveryReport,
)
from ofi.services.recovery_health import RecoveryHealthThresholds
from ofi.services.recovery_runtime import run_recovery_cycle


class FakeWorker:
    def __init__(self, report):
        self.report = report
        self.kwargs = None

    def run_once(self, **kwargs):
        self.kwargs = kwargs
        return self.report


def thresholds():
    return RecoveryHealthThresholds(
        warning_errors=1,
        critical_errors=3,
        warning_unknown=1,
        critical_unknown=3,
        warning_pending=2,
        critical_pending=4,
    )


def test_runtime_runs_worker_assesses_and_logs_one_event(caplog):
    report = DispatchRecoveryReport(
        candidates=1,
        items=(
            DispatchRecoveryItem(
                attempt_id="private-attempt",
                provider_id="private-provider",
                status="unknown",
            ),
        ),
    )
    worker = FakeWorker(report)
    moment = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    logger = logging.getLogger("ofi.test.recovery_runtime")

    with caplog.at_level(logging.WARNING, logger="ofi.test.recovery_runtime"):
        result = run_recovery_cycle(
            worker,
            thresholds=thresholds(),
            logger=logger,
            now=moment,
            stale_after=timedelta(minutes=7),
            limit=12,
        )

    assert worker.kwargs == {
        "now": moment,
        "stale_after": timedelta(minutes=7),
        "limit": 12,
    }
    assert result.report is report
    assert result.assessment.status == "warning"
    assert result.event["event"] == "ofi.dispatch_recovery.health"
    assert result.event["observed_at"] == "2026-10-10T12:00:00+00:00"
    assert len(caplog.records) == 1
    assert caplog.records[0].ofi_recovery_health == result.event
    serialized = str(result.event)
    assert "private-attempt" not in serialized
    assert "private-provider" not in serialized


def test_runtime_rejects_naive_clock_before_running_worker():
    worker = FakeWorker(DispatchRecoveryReport(candidates=0, items=()))

    with pytest.raises(ValueError, match="timezone-aware"):
        run_recovery_cycle(
            worker,
            thresholds=thresholds(),
            logger=logging.getLogger("ofi.test.recovery_runtime"),
            now=datetime(2026, 10, 10, 12, 0),
        )

    assert worker.kwargs is None


def test_runtime_logs_healthy_cycle_at_info(caplog):
    worker = FakeWorker(DispatchRecoveryReport(candidates=0, items=()))
    logger = logging.getLogger("ofi.test.recovery_runtime.healthy")

    with caplog.at_level(logging.INFO, logger="ofi.test.recovery_runtime.healthy"):
        result = run_recovery_cycle(
            worker,
            thresholds=thresholds(),
            logger=logger,
            now=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        )

    assert result.assessment.status == "healthy"
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.INFO
