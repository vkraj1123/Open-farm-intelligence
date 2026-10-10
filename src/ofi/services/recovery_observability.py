"""Structured logging helpers for dispatch recovery health.

This module adapts a health assessment to Python's standard logging API. It
does not configure handlers, exporters, alert receivers, or scheduler cadence.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from ofi.services.recovery_health import RecoveryHealthAssessment


_EVENT_NAME = "ofi.dispatch_recovery.health"


def build_recovery_health_event(
    assessment: RecoveryHealthAssessment,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable, low-cardinality health event.

    The event intentionally excludes provider messages, provider IDs, attempt
    IDs, transaction IDs, and raw exception strings. Consumers can serialize
    this record as JSON or forward it to an existing metrics/logging pipeline.
    """

    moment = observed_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")

    return {
        "event": _EVENT_NAME,
        "observed_at": moment.astimezone(timezone.utc).isoformat(),
        "status": assessment.status,
        "metrics": dict(assessment.metrics),
        "alerts": [
            {
                "code": alert.code,
                "severity": alert.severity,
                "observed": alert.observed,
                "threshold": alert.threshold,
            }
            for alert in assessment.alerts
        ],
    }


def log_recovery_health(
    assessment: RecoveryHealthAssessment,
    *,
    logger: logging.Logger,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Log one structured event and return the exact safe event payload."""

    event = build_recovery_health_event(assessment, observed_at=observed_at)
    level = {
        "healthy": logging.INFO,
        "warning": logging.WARNING,
        "critical": logging.ERROR,
    }[assessment.status]
    logger.log(level, _EVENT_NAME, extra={"ofi_recovery_health": event})
    return event
