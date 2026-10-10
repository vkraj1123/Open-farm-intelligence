import json
import logging
from datetime import datetime, timezone

import pytest

from ofi.services.dispatch_recovery_worker import DispatchRecoveryItem, DispatchRecoveryReport
from ofi.services.recovery_health import RecoveryHealthThresholds, assess_recovery_health
from ofi.services.recovery_observability import (
    build_recovery_health_event,
    log_recovery_health,
)


def make_assessment(*, status="unknown", error=None):
    report = DispatchRecoveryReport(
        candidates=1,
        items=(DispatchRecoveryItem(
            attempt_id="private-attempt-id",
            provider_id="private-provider-id",
            status=status,
            error=error,
        ),),
    )
    return assess_recovery_health(
        report,
        thresholds=RecoveryHealthThresholds(
            warning_errors=1,
            critical_errors=3,
            warning_unknown=1,
            critical_unknown=3,
            warning_pending=2,
            critical_pending=4,
        ),
    )


def test_event_is_json_serializable_and_normalizes_timestamp_to_utc():
    event = build_recovery_health_event(
        make_assessment(),
        observed_at=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert event["event"] == "ofi.dispatch_recovery.health"
    assert event["observed_at"] == "2026-10-10T12:00:00+00:00"
    assert event["status"] == "warning"
    assert event["metrics"]["unknown"] == 1
    assert event["alerts"][0]["code"] == "unresolved_unknown"
    json.dumps(event)


def test_event_excludes_provider_attempt_and_exception_details():
    assessment = make_assessment(
        error="sensitive remote response body",
    )
    event = build_recovery_health_event(assessment)

    serialized = json.dumps(event)
    assert "private-attempt-id" not in serialized
    assert "private-provider-id" not in serialized
    assert "sensitive remote response body" not in serialized


def test_logger_receives_structured_event_and_level(caplog):
    logger = logging.getLogger("ofi.test.recovery")
    assessment = make_assessment()

    with caplog.at_level(logging.WARNING, logger="ofi.test.recovery"):
        event = log_recovery_health(
            assessment,
            logger=logger,
            observed_at=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "ofi.dispatch_recovery.health"
    assert record.ofi_recovery_health == event
    assert record.ofi_recovery_health["status"] == "warning"


def test_naive_observation_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        build_recovery_health_event(
            make_assessment(),
            observed_at=datetime(2026, 10, 10, 12, 0),
        )
