from types import SimpleNamespace

import pytest

from ofi.services.dispatch_recovery_worker import (
    DispatchRecoveryItem,
    DispatchRecoveryReport,
)
from ofi.services.recovery_health import (
    RecoveryHealthThresholds,
    assess_recovery_health,
)


def thresholds():
    return RecoveryHealthThresholds(
        warning_errors=1,
        critical_errors=3,
        warning_unknown=1,
        critical_unknown=3,
        warning_pending=2,
        critical_pending=4,
    )


def report(*items):
    return DispatchRecoveryReport(candidates=len(items), items=tuple(items))


def test_recovery_health_is_healthy_when_no_operational_signals_exist():
    assessment = assess_recovery_health(report(), thresholds=thresholds())

    assert assessment.status == "healthy"
    assert assessment.alerts == ()
    assert assessment.metrics["candidates"] == 0


def test_recovery_health_emits_warning_at_configured_error_threshold():
    assessment = assess_recovery_health(
        report(DispatchRecoveryItem(
            attempt_id="attempt-1",
            provider_id="provider-1",
            status="unknown",
            error="private provider details must not be surfaced",
        )),
        thresholds=thresholds(),
    )

    assert assessment.status == "warning"
    assert [(a.code, a.severity, a.observed, a.threshold) for a in assessment.alerts] == [
        ("reconciliation_errors", "warning", 1, 1),
        ("unresolved_unknown", "warning", 1, 1),
    ]
    assert "private provider details" not in repr(assessment)


def test_recovery_health_critical_signal_takes_precedence_over_warnings():
    items = tuple(
        DispatchRecoveryItem(
            attempt_id=f"attempt-{i}",
            provider_id="provider-1",
            status="unknown",
        )
        for i in range(3)
    )
    assessment = assess_recovery_health(report(*items), thresholds=thresholds())

    assert assessment.status == "critical"
    assert assessment.alerts == (
        assessment.alerts[0],
    )
    assert assessment.alerts[0].code == "unresolved_unknown"
    assert assessment.alerts[0].severity == "critical"
    assert assessment.alerts[0].observed == 3


def test_pending_reconciliation_has_its_own_threshold():
    item = DispatchRecoveryItem(
        attempt_id="attempt-pending",
        provider_id="provider-1",
        status="unknown",
        result=SimpleNamespace(evidence=SimpleNamespace(status="pending")),
    )
    assessment = assess_recovery_health(
        report(item, item),
        thresholds=thresholds(),
    )

    assert assessment.status == "warning"
    assert assessment.alerts[0].code == "pending_reconciliation"
    assert assessment.alerts[0].observed == 2


@pytest.mark.parametrize(
    "values",
    [
        (0, 1, 1, 2, 1, 2),
        (2, 1, 1, 2, 1, 2),
        (1, 2, 3, 2, 1, 2),
    ],
)
def test_recovery_health_rejects_invalid_thresholds(values):
    with pytest.raises(ValueError):
        RecoveryHealthThresholds(*values)
