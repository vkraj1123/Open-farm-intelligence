"""Signed HTTPS webhook delivery for tenant invitations.

The configured receiver is responsible for mapping an immutable identity-provider
subject to an approved delivery channel. OFI sends the one-time token only to
this explicitly configured HTTPS endpoint and never logs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import math
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_EVENT_NAME = "ofi.tenant_invitation.created"


class InvitationDeliveryError(RuntimeError):
    """Sanitized failure delivering a tenant invitation."""


@dataclass(frozen=True)
class InvitationDeliveryReceipt:
    event_id: str
    status_code: int


class _NoRedirectHandler(HTTPRedirectHandler):
    """Never forward one-time invitation credentials to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _https_transport(request: Request, timeout_seconds: float) -> int:
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return int(response.getcode())
    except HTTPError as exc:
        return int(exc.code)


class TenantInvitationDeliveryWebhook:
    """Deliver a subject-bound invitation using HTTPS and HMAC-SHA256.

    The receiver verifies \`X-OFI-Signature\` over the exact UTF-8 body and
    deduplicates on \`X-OFI-Event-ID\`. The payload contains the invitation token,
    so this endpoint must be a trusted bridge with secret-safe logs/storage.
    Redirects, non-2xx responses and transport failures are not retried.
    """

    def __init__(
        self,
        endpoint: str,
        secret: str,
        *,
        timeout_seconds: float = 5.0,
        transport: Callable[[Request, float], int] | None = None,
    ) -> None:
        raw_endpoint = endpoint.strip() if isinstance(endpoint, str) else ""
        parsed = urlsplit(raw_endpoint)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("invitation delivery endpoint has an invalid port") from exc
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "invitation delivery endpoint must be an absolute HTTPS URL without credentials, query, or fragment"
            )
        if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
            raise ValueError("invitation delivery secret must be at least 32 UTF-8 bytes")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("invitation delivery timeout must be a finite positive number")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 30:
            raise ValueError("invitation delivery timeout must be greater than 0 and at most 30 seconds")
        self._endpoint = raw_endpoint
        self._secret = secret.encode("utf-8")
        self._timeout = timeout
        self._transport = transport or _https_transport

    def send_invitation(
        self,
        *,
        invitation_id: str,
        tenant_id: str,
        subject_id: str,
        invitation_token: str,
        expires_at: datetime,
    ) -> InvitationDeliveryReceipt:
        values = {
            "invitation_id": invitation_id,
            "tenant_id": tenant_id,
            "subject_id": subject_id,
        }
        for name, value in values.items():
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
                raise ValueError(f"{name} must be non-empty and at most 512 characters")
        if not isinstance(invitation_token, str) or not 32 <= len(invitation_token) <= 256:
            raise ValueError("invitation token length is invalid")
        if (
            not isinstance(expires_at, datetime)
            or expires_at.tzinfo is None
            or expires_at.utcoffset() is None
        ):
            raise ValueError("invitation expiry must be timezone-aware")

        body_payload = {
            "event": _EVENT_NAME,
            "version": 1,
            "invitation_id": invitation_id.strip(),
            "tenant_id": tenant_id.strip(),
            "subject_id": subject_id.strip(),
            "invitation_token": invitation_token,
            "expires_at": expires_at.isoformat(),
            "redemption_path": f"/tenants/{tenant_id.strip()}/invitations/redeem",
        }
        body = json.dumps(
            body_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
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
                "User-Agent": "open-farm-intelligence-invitations/1",
                "X-OFI-Event": _EVENT_NAME,
                "X-OFI-Event-ID": event_id,
                "X-OFI-Signature": f"sha256={signature}",
            },
        )
        try:
            status_code = int(self._transport(request, self._timeout))
        except Exception:
            # Transport exceptions may contain private hostnames, headers, or tokens.
            raise InvitationDeliveryError("tenant invitation webhook transport failed") from None
        if not 200 <= status_code < 300:
            raise InvitationDeliveryError(
                f"tenant invitation webhook returned HTTP status {status_code}"
            )
        return InvitationDeliveryReceipt(event_id=event_id, status_code=status_code)
