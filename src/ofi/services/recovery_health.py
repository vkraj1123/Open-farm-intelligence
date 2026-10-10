"""Deterministic health assessment for scheduled dispatch recovery.

Thresholds are deployment inputs, not universal defaults. This module reports
operational signals only; it never changes execution state or triggers retries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ofi.services.dispatch_recovery_worker import DispatchRecoveryReport


AlertSeverity = Literal["warning", "critical"]
HealthStatus = Literal["healthy", "warning", "critical"]


@dataclass(frozen=True)
class RecoveryHealthThresholds:
    """Explicit per-deployment thresholds for recovery worker counters."""

    warning_errors: int
    critical_errors: int
    warning_unknown: int
    critical_unknown: int
    warning_pending: int
    critical_pending: int

    def __post_init__(self) -> None:
        pairs = (
            ("errors", self.warning_errors, self.critical_errors),
            ("unknown", self.warning_unknown, self.critical_unknown),
            ("pending", self.warning_pending, self.critical_pending),
        )
        for name, warning, critical in pairs:
            if warning < 1 or critical < 1:
                raise ValueError(f"{name} thresholds must be positive")
            if critical < warning:
                raise ValueError(
                    f"critical {name} threshold must be greater than or equal "
                    "to its warning threshold"
                )


@dataclass(frozen=True)
class RecoveryHealthAlert:
    """A low-cardinality operational signal; contains no provider payload."""

    code: str
    severity: AlertSeverity
    observed: int
    threshold: int


@dataclass(frozen=True)
class RecoveryHealthAssessment:
    status: HealthStatus
    alerts: tuple[RecoveryHealthAlert, ...]
    metrics: dict[str, int]


def assess_recovery_health(
    report: DispatchRecoveryReport,
    *,
    thresholds: RecoveryHealthThresholds,
) -> RecoveryHealthAssessment:
    """Assess one worker run against explicit deployment thresholds.

    Counters are independent: an item may be both unknown and an error. The
    assessment does not infer queue backlog from per-run candidate counts and
    does not log or expose provider error messages.
    """

    metrics = report.metrics
    rules = (
        ("reconciliation_errors", metrics["errors"],
         thresholds.warning_errors, thresholds.critical_errors),
        ("unresolved_unknown", metrics["unknown"],
         thresholds.warning_unknown, thresholds.critical_unknown),
        ("pending_reconciliation", metrics["pending"],
         thresholds.warning_pending, thresholds.critical_pending),
    )
    alerts: list[RecoveryHealthAlert] = []
    for code, observed, warning_threshold, critical_threshold in rules:
        if observed >= critical_threshold:
            alerts.append(RecoveryHealthAlert(
                code=code,
                severity="critical",
                observed=observed,
                threshold=critical_threshold,
            ))
        elif observed >= warning_threshold:
            alerts.append(RecoveryHealthAlert(
                code=code,
                severity="warning",
                observed=observed,
                threshold=warning_threshold,
            ))

    status: HealthStatus
    if any(alert.severity == "critical" for alert in alerts):
        status = "critical"
    elif alerts:
        status = "warning"
    else:
        status = "healthy"

    return RecoveryHealthAssessment(
        status=status,
        alerts=tuple(alerts),
        metrics=dict(metrics),
    )
