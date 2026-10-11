"""Signed HTTPS webhook delivery for dispatch-recovery health alerts.

The webhook contract is intentionally small and generic so deployments can
bridge OFI alerts to their own incident system without coupling the core to a
specific vendor. Only sanitized aggregate health events are eligible.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlsplit


_EVENT_NAME = "ofi.dispatch_recovery.health"
_ALLOWED_STATUSES = {"warning", "critical"}
_ALLOWED_METRICS = {
    "candidates",
    "reconciled",
    "errors",
    "confirmed_executed",
    "confirmed_not_executed",
    "pending",
    "unknown",
}
_ALLOWED_ALERTS = {
    "reconciliation_errors",
    "unresolved_unknown",
    "pending_reconciliation",
}


class RecoveryAlertDeliveryError(RuntimeError):
    """Sanitized failure delivering a recovery health event."""


@dataclass(frozen=True)
class RecoveryAlertDelivery:
    """Non-sensitive receipt for an accepted webhook request."""

    event_id: str
    status_code: int


def _validated_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    """Project a health event onto a strict allow-list before external delivery."""
    if event.get("event") != _EVENT_NAME:
        raise ValueError("unsupported recovery alert event")
    status = event.get("status")
    if status not in _ALLOWED_STATUSES:
        raise ValueError("only warning or critical health events can be delivered")

    observed_at = event.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError("recovery alert event needs an observation timestamp")

    raw_metrics = event.get("metrics")
    if not isinstance(raw_metrics, Mapping):
        raise ValueError("recovery alert event metrics must be an object")
    metrics: dict[str, int] = {}
    for key, value in raw_metrics.items():
        if key not in _ALLOWED_METRICS:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("recovery alert metrics must be non-negative integers")
        metrics[key] = value

    raw_alerts = event.get("alerts")
    if not isinstance(raw_alerts, (list, tuple)) or not raw_alerts:
        raise ValueError("warning and critical events must include alert signals")
    alerts: list[dict[str, Any]] = []
    for alert in raw_alerts:
        if not isinstance(alert, Mapping):
            raise ValueError("recovery alert signal must be an object")
        code = alert.get("code")
        severity = alert.get("severity")
        observed = alert.get("observed")
        threshold = alert.get("threshold")
        if code not in _ALLOWED_ALERTS or severity not in _ALLOWED_STATUSES:
            raise ValueError("recovery alert signal has an unsupported code or severity")
        if (
            isinstance(observed, bool)
            or not isinstance(observed, int)
            or observed < 0
            or isinstance(threshold, bool)
            or not isinstance(threshold, int)
            or threshold < 1
        ):
            raise ValueError("recovery alert observed and threshold values are invalid")
        alerts.append(
            {
                "code": code,
                "severity": severity,
                "observed": observed,
                "threshold": threshold,
            }
        )

    if status == "critical" and not any(
        alert["severity"] == "critical" for alert in alerts
    ):
        raise ValueError("critical event must contain a critical alert signal")
    if status == "warning" and not any(
        alert["severity"] == "warning" for alert in alerts
    ):
        raise ValueError("warning event must contain a warning alert signal")

    # Any additional caller-supplied fields (IDs, provider payloads, error text)
    # are deliberately discarded rather than forwarded to the receiver.
    return {
        "event": _EVENT_NAME,
        "observed_at": observed_at,
        "status": status,
        "metrics": metrics,
        "alerts": alerts,
    }


class _NoRedirectHandler(HTTPRedirectHandler):
    """Do not forward signed payloads to an endpoint's redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _https_transport(request: Request, timeout_seconds: float) -> int:
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return int(response.getcode())
    except HTTPError as exc:
        # HTTPError can represent 3xx (redirect) as well as 4xx/5xx responses.
        # Do not follow it; let the caller treat every non-2xx as delivery failure.
        return int(exc.code)


class RecoveryAlertWebhook:
    """Send a sanitized health alert with HMAC-SHA256 request authentication.

    Receiver contract:
      - HTTPS POST with UTF-8 JSON body.
      - X-OFI-Signature: "sha256=<hex HMAC of exact request body>".
      - X-OFI-Event-ID: SHA-256 of the exact request body for receiver deduplication.
      - Any 2xx status confirms delivery; redirects and all other statuses fail.

    This is at-most-one send per process invocation. It deliberately does not
    retry ambiguous network errors, because the receiver may already have
    accepted the first request. Receiver-side event-ID deduplication is advised.
    """

    def __init__(
        self,
        endpoint: str,
        secret: str,
        *,
        timeout_seconds: float = 5.0,
        transport: Callable[[Request, float], int] | None = None,
    ) -> None:
        raw_endpoint = endpoint.strip()
        parsed = urlsplit(raw_endpoint)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("alert endpoint has an invalid port") from exc
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "alert endpoint must be an absolute HTTPS URL without credentials, query, or fragment"
            )
        if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
            raise ValueError("alert webhook secret must be at least 32 UTF-8 bytes")
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise ValueError("alert webhook timeout must be a finite positive number")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("alert webhook timeout must be a finite positive number")
        self._endpoint = raw_endpoint
        self._secret = secret.encode("utf-8")
        self._timeout = timeout
        self._transport = transport or _https_transport

    def send(self, event: Mapping[str, Any]) -> RecoveryAlertDelivery:
        """Deliver one warning/critical event or raise a sanitized error."""
        payload = _validated_payload(event)
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        event_id = hashlib.sha256(body).hexdigest()
        signature = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        request = Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "open-farm-intelligence-recovery/1",
                "X-OFI-Signature": f"sha256={signature}",
                "X-OFI-Event-ID": event_id,
            },
        )
        try:
            status_code = int(self._transport(request, self._timeout))
        except Exception:
            # Network errors can contain private hostnames, query material, or
            # receiver diagnostics. Avoid serializing their text into CLI logs.
            raise RecoveryAlertDeliveryError(
                "recovery alert webhook transport failed"
            ) from None
        if not 200 <= status_code < 300:
            raise RecoveryAlertDeliveryError(
                f"recovery alert webhook returned HTTP status {status_code}"
            )
        return RecoveryAlertDelivery(event_id=event_id, status_code=status_code)
