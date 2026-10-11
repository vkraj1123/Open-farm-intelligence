import hashlib
import hmac
import json
from urllib.request import Request

import pytest

from ofi.services.recovery_alerts import (
    RecoveryAlertDeliveryError,
    RecoveryAlertWebhook,
)


SECRET = "s" * 40


def warning_event():
    return {
        "event": "ofi.dispatch_recovery.health",
        "observed_at": "2026-10-11T00:00:00+00:00",
        "status": "warning",
        "metrics": {
            "candidates": 3,
            "reconciled": 1,
            "errors": 1,
            "confirmed_executed": 0,
            "confirmed_not_executed": 0,
            "pending": 0,
            "unknown": 1,
            "untrusted_extra_metric": 99,
        },
        "alerts": [
            {
                "code": "reconciliation_errors",
                "severity": "warning",
                "observed": 1,
                "threshold": 1,
                "internal_note": "must not leave process",
            }
        ],
        "provider_id": "private-provider",
        "attempt_id": "private-attempt",
        "error_text": "private remote response",
    }


def test_webhook_sends_sanitized_payload_with_hmac_and_deduplication_id():
    calls = []

    def transport(request: Request, timeout: float) -> int:
        calls.append((request, timeout))
        return 202

    webhook = RecoveryAlertWebhook(
        "https://alerts.example.test/ofi",
        SECRET,
        timeout_seconds=2.5,
        transport=transport,
    )
    receipt = webhook.send(warning_event())

    assert len(calls) == 1
    request, timeout = calls[0]
    body = request.data
    payload = json.loads(body)
    assert timeout == 2.5
    assert request.full_url == "https://alerts.example.test/ofi"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("X-ofi-signature") == (
        "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    )
    expected_event_id = hashlib.sha256(body).hexdigest()
    assert request.get_header("X-ofi-event-id") == expected_event_id
    assert receipt.event_id == expected_event_id
    assert receipt.status_code == 202
    assert payload["status"] == "warning"
    assert payload["metrics"]["errors"] == 1
    assert "untrusted_extra_metric" not in payload["metrics"]
    assert "provider_id" not in payload
    assert "attempt_id" not in payload
    assert "error_text" not in payload
    assert "internal_note" not in json.dumps(payload)
    assert "private-provider" not in body.decode()
    assert "private-attempt" not in body.decode()
    assert "private remote response" not in body.decode()


def test_webhook_rejects_http_credentials_query_and_fragment_urls():
    for endpoint in (
        "http://alerts.example.test/ofi",
        "https://user:password@alerts.example.test/ofi",
        "https://alerts.example.test/ofi?token=secret",
        "https://alerts.example.test/ofi#section",
    ):
        with pytest.raises(ValueError, match="absolute HTTPS URL"):
            RecoveryAlertWebhook(endpoint, SECRET)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_webhook_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="timeout"):
        RecoveryAlertWebhook(
            "https://alerts.example.test/ofi",
            SECRET,
            timeout_seconds=timeout,
        )


def test_webhook_requires_minimum_secret_length():
    with pytest.raises(ValueError, match="at least 32"):
        RecoveryAlertWebhook(
            "https://alerts.example.test/ofi",
            "too-short",
        )


def test_webhook_rejects_success_status_without_alert_signals():
    event = warning_event()
    event["alerts"] = []
    with pytest.raises(ValueError, match="must include alert signals"):
        RecoveryAlertWebhook(
            "https://alerts.example.test/ofi", SECRET, transport=lambda _r, _t: 204
        ).send(event)


def test_webhook_treats_non_2xx_as_delivery_failure_without_response_body():
    with pytest.raises(RecoveryAlertDeliveryError, match="HTTP status 503") as error:
        RecoveryAlertWebhook(
            "https://alerts.example.test/ofi",
            SECRET,
            transport=lambda _r, _t: 503,
        ).send(warning_event())
    assert "secret response body" not in str(error.value)


def test_webhook_sanitizes_transport_exception_details():
    def transport(_request, _timeout):
        raise OSError("private-host.example.test password=do-not-log")

    with pytest.raises(RecoveryAlertDeliveryError, match="transport failed") as error:
        RecoveryAlertWebhook(
            "https://alerts.example.test/ofi",
            SECRET,
            transport=transport,
        ).send(warning_event())

    assert "private-host" not in str(error.value)
    assert "do-not-log" not in str(error.value)


def test_webhook_projects_payload_and_does_not_forward_unrecognized_alert_fields():
    event = warning_event()
    event["alerts"][0]["transaction_id"] = "secret-transaction"

    webhook = RecoveryAlertWebhook(
        "https://alerts.example.test/ofi",
        SECRET,
        transport=lambda request, _timeout: (
            200 if "secret-transaction" not in request.data.decode() else 500
        ),
    )
    assert webhook.send(event).status_code == 200
