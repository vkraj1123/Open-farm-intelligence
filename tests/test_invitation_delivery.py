import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.request import Request

import pytest

from ofi.services.invitation_delivery import (
    InvitationDeliveryError,
    TenantInvitationDeliveryWebhook,
)


SECRET = "delivery-webhook-secret-long-enough-0123456789"
EXPIRY = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def send_args():
    return {
        "invitation_id": "invite-123",
        "tenant_id": "tenant-a",
        "subject_id": "issuer|user-abc",
        "invitation_token": "x" * 44,
        "expires_at": EXPIRY,
    }


def test_webhook_signs_exact_body_and_sends_only_allowlisted_invitation_fields():
    calls = []

    def transport(request: Request, timeout: float) -> int:
        calls.append((request, timeout))
        return 202

    webhook = TenantInvitationDeliveryWebhook(
        "https://delivery.example.test/ofi/tenant-invitations",
        SECRET,
        timeout_seconds=3,
        transport=transport,
    )
    receipt = webhook.send_invitation(**send_args())
    request, timeout = calls[0]
    body = request.data
    payload = json.loads(body)

    assert len(calls) == 1
    assert timeout == 3
    assert request.full_url == "https://delivery.example.test/ofi/tenant-invitations"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("X-ofi-event") == "ofi.tenant_invitation.created"
    assert request.get_header("X-ofi-signature") == (
        "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    )
    assert request.get_header("X-ofi-event-id") == hashlib.sha256(body).hexdigest()
    assert receipt.event_id == hashlib.sha256(body).hexdigest()
    assert receipt.status_code == 202
    assert payload == {
        "event": "ofi.tenant_invitation.created",
        "version": 1,
        "invitation_id": "invite-123",
        "tenant_id": "tenant-a",
        "subject_id": "issuer|user-abc",
        "invitation_token": "x" * 44,
        "expires_at": EXPIRY.isoformat(),
        "redemption_path": "/tenants/tenant-a/invitations/redeem",
    }


@pytest.mark.parametrize("endpoint", [
    "http://delivery.example.test/hook",
    "https://user:password@delivery.example.test/hook",
    "https://delivery.example.test/hook?token=secret",
    "https://delivery.example.test/hook#fragment",
    "https://delivery.example.test:bad/hook",
])
def test_webhook_rejects_insecure_or_ambiguous_endpoints(endpoint):
    with pytest.raises(ValueError, match="endpoint"):
        TenantInvitationDeliveryWebhook(endpoint, SECRET)


@pytest.mark.parametrize("secret", ["short", "s" * 31])
def test_webhook_rejects_short_secrets(secret):
    with pytest.raises(ValueError, match="at least 32"):
        TenantInvitationDeliveryWebhook("https://delivery.example.test/hook", secret)


@pytest.mark.parametrize("timeout", [0, -1, 31, float("inf"), float("nan"), True])
def test_webhook_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="timeout"):
        TenantInvitationDeliveryWebhook(
            "https://delivery.example.test/hook", SECRET, timeout_seconds=timeout
        )


def test_webhook_fails_on_non_2xx_and_does_not_expose_response_body():
    with pytest.raises(InvitationDeliveryError, match="HTTP status 503") as exc:
        TenantInvitationDeliveryWebhook(
            "https://delivery.example.test/hook",
            SECRET,
            transport=lambda _request, _timeout: 503,
        ).send_invitation(**send_args())
    assert "private response body" not in str(exc.value)


def test_webhook_sanitizes_transport_errors_that_may_contain_secrets():
    def transport(_request, _timeout):
        raise OSError("token=" + "x" * 44 + " private-host.example")

    with pytest.raises(InvitationDeliveryError, match="transport failed") as exc:
        TenantInvitationDeliveryWebhook(
            "https://delivery.example.test/hook", SECRET, transport=transport
        ).send_invitation(**send_args())
    assert "private-host" not in str(exc.value)
    assert "token=" not in str(exc.value)
    assert "x" * 44 not in str(exc.value)


def test_webhook_requires_timezone_aware_expiry():
    args = send_args()
    args["expires_at"] = datetime(2026, 10, 16, 12, 0)
    webhook = TenantInvitationDeliveryWebhook(
        "https://delivery.example.test/hook", SECRET, transport=lambda _r, _t: 202
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        webhook.send_invitation(**args)
