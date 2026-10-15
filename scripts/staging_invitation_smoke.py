#!/usr/bin/env python3
"""Protected, phone-launchable acceptance check for an OFI staging deployment.

This creates one real, one-hour staging invitation. It never prints bearer
credentials or API response bodies. It requires an explicit confirmation.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 1_000_000
EVENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class SmokeCheckError(RuntimeError):
    """Safe-to-display error whose text contains no credentials or response body."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        # Do not forward the staging bearer token to a redirect target.
        return None


def validate_base_url(raw: str) -> str:
    """Accept only a root HTTPS origin; reject obvious local/private targets."""
    if not raw or raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise ValueError("staging base URL is missing or contains whitespace")
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        _ = parsed.port  # Force invalid-port validation.
    except ValueError as exc:
        raise ValueError("staging base URL is malformed") from exc
    if parsed.scheme.lower() != "https" or not hostname:
        raise ValueError("staging base URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("staging base URL must not contain URL credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("staging base URL must not contain a query or fragment")
    if parsed.path not in ("", "/"):
        raise ValueError("staging base URL must be the origin, without a path prefix")
    host = hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("staging base URL must not target a local host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
        raise ValueError("staging base URL must not target a private or special-use IP address")
    return urlunsplit(("https", parsed.netloc, "", "", "")).rstrip("/")


def contains_sensitive_field(value: object) -> bool:
    """Detect token/secret-like fields recursively without inspecting values."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if "token" in normalized or "secret" in normalized:
                return True
            if contains_sensitive_field(child):
                return True
    elif isinstance(value, list):
        return any(contains_sensitive_field(item) for item in value)
    return False


def _required_environment() -> tuple[str, str, str, str]:
    if os.getenv("OFI_STAGING_CONFIRMATION") != "RUN-STAGING-INVITATION-CHECK":
        raise SmokeCheckError(
            "explicit confirmation required: enter RUN-STAGING-INVITATION-CHECK in the workflow form"
        )
    try:
        base_url = validate_base_url(os.getenv("OFI_STAGING_BASE_URL", ""))
    except ValueError as exc:
        raise SmokeCheckError(str(exc)) from None
    admin_token = os.getenv("OFI_STAGING_ADMIN_BEARER_TOKEN", "")
    tenant_id = os.getenv("OFI_STAGING_TENANT_ID", "").strip()
    subject_id = os.getenv("OFI_STAGING_INVITED_SUBJECT_ID", "").strip()
    if not admin_token or admin_token != admin_token.strip() or any(c.isspace() for c in admin_token):
        raise SmokeCheckError("staging administrator bearer-token secret is missing or malformed")
    if not tenant_id or not subject_id:
        raise SmokeCheckError("staging tenant ID and invited subject ID must be configured")
    return base_url, admin_token, tenant_id, subject_id


def _request_json(
    opener,
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    bearer_token: str | None = None,
    payload: dict | None = None,
) -> tuple[int, dict, dict[str, str]]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if bearer_token is not None:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = Request(base_url + path, data=body, headers=headers, method=method)
    try:
        response = opener.open(request, timeout=12)
    except HTTPError as exc:
        # Never print upstream response content: error pages can echo request data.
        raise SmokeCheckError(f"staging API returned HTTP {exc.code} for {method} {path.split('?')[0]}") from None
    except (URLError, TimeoutError, OSError):
        raise SmokeCheckError(
            f"staging API network/TLS request failed for {method} {path.split('?')[0]}; details withheld"
        ) from None
    except Exception:
        raise SmokeCheckError(
            f"staging API request failed for {method} {path.split('?')[0]}; details withheld"
        ) from None

    with response:
        status = response.status
        response_headers = {k.lower(): v for k, v in response.headers.items()}
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SmokeCheckError("staging API response exceeded the permitted size")
    if not raw:
        document = {}
    else:
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SmokeCheckError("staging API returned a non-JSON or invalid UTF-8 response") from None
    if not isinstance(document, dict):
        raise SmokeCheckError("staging API response was not a JSON object")
    return status, document, response_headers


def _list_invitations(opener, base_url: str, tenant_id: str, admin_token: str) -> list[dict]:
    encoded_tenant = quote(tenant_id, safe="")
    status, document, _ = _request_json(
        opener, base_url, f"/tenants/{encoded_tenant}/invitations?limit=500",
        bearer_token=admin_token,
    )
    if status != 200:
        raise SmokeCheckError(f"invitation-list check returned HTTP {status}")
    items = document.get("invitations")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise SmokeCheckError("invitation-list response did not match the expected metadata contract")
    if contains_sensitive_field(items):
        raise SmokeCheckError("invitation-list response unexpectedly contained a token/secret field")
    return items


def _revoke_best_effort(opener, base_url: str, tenant_id: str, invitation_id: str, admin_token: str) -> bool:
    try:
        encoded_tenant = quote(tenant_id, safe="")
        encoded_invitation = quote(invitation_id, safe="")
        status, _, _ = _request_json(
            opener, base_url,
            f"/tenants/{encoded_tenant}/invitations/{encoded_invitation}/revoke",
            method="POST", bearer_token=admin_token, payload={},
        )
        return status == 200
    except Exception:
        return False


def run_check() -> str:
    base_url, admin_token, tenant_id, subject_id = _required_environment()
    opener = build_opener(_NoRedirect)

    status, health, _ = _request_json(opener, base_url, "/health")
    if status != 200 or health.get("status") != "ok":
        raise SmokeCheckError("liveness check did not pass")
    status, readiness, _ = _request_json(opener, base_url, "/ready")
    if status != 200 or readiness.get("status") != "ready":
        raise SmokeCheckError("readiness check did not pass; persistent storage may be unavailable")

    existing = _list_invitations(opener, base_url, tenant_id, admin_token)
    if any(
        item.get("invited_actor_id") == subject_id and item.get("status") == "pending"
        for item in existing
    ):
        raise SmokeCheckError(
            "the configured subject already has a pending invitation; inspect/revoke it or use a fresh staging subject"
        )

    encoded_tenant = quote(tenant_id, safe="")
    try:
        status, created, headers = _request_json(
            opener, base_url, f"/tenants/{encoded_tenant}/invitations",
            method="POST", bearer_token=admin_token,
            payload={"actor_id": subject_id, "expires_in_hours": 1},
        )
    except SmokeCheckError:
        # A network failure can be ambiguous: the server may have created the
        # invitation. Reconcile by listing and revoke only new pending rows for
        # this exact subject, if they can be identified unambiguously.
        try:
            latest = _list_invitations(opener, base_url, tenant_id, admin_token)
            prior_ids = {item.get("invitation_id") for item in existing}
            new_items = [
                item for item in latest
                if item.get("invited_actor_id") == subject_id
                and item.get("status") == "pending"
                and item.get("invitation_id") not in prior_ids
            ]
            if len(new_items) == 1 and new_items[0].get("invitation_id"):
                if not _revoke_best_effort(
                    opener, base_url, tenant_id, str(new_items[0]["invitation_id"]), admin_token
                ):
                    raise SmokeCheckError("creation outcome was ambiguous and cleanup could not be confirmed")
        except SmokeCheckError as cleanup_error:
            if "cleanup could not be confirmed" in str(cleanup_error):
                raise
        raise SmokeCheckError(
            "invitation-creation request failed; check pending invitations before re-running"
        ) from None

    invitation = created.get("invitation")
    invitation_id = invitation.get("invitation_id") if isinstance(invitation, dict) else None
    try:
        if status != 201:
            raise SmokeCheckError(f"invitation creation returned HTTP {status}")
        if headers.get("cache-control", "").lower() != "no-store, private":
            raise SmokeCheckError("creation response is missing the required Cache-Control policy")
        if headers.get("pragma", "").lower() != "no-cache":
            raise SmokeCheckError("creation response is missing the required Pragma policy")
        if contains_sensitive_field(created):
            raise SmokeCheckError("webhook-mode response unexpectedly contained a token/secret field")
        if not isinstance(invitation, dict) or not invitation_id:
            raise SmokeCheckError("creation response did not include invitation metadata")
        if invitation.get("tenant_id") != tenant_id or invitation.get("invited_actor_id") != subject_id:
            raise SmokeCheckError("created invitation metadata did not match the requested staging target")
        if invitation.get("status") != "pending":
            raise SmokeCheckError("new invitation did not remain pending after delivery acceptance")
        if created.get("delivery") != "accepted":
            raise SmokeCheckError("API did not confirm webhook delivery acceptance; webhook mode may be disabled")
        event_id = created.get("delivery_event_id")
        if not isinstance(event_id, str) or not EVENT_ID_RE.fullmatch(event_id):
            raise SmokeCheckError("delivery event ID is missing or malformed")

        after = _list_invitations(opener, base_url, tenant_id, admin_token)
        match = next((item for item in after if item.get("invitation_id") == invitation_id), None)
        if not match or match.get("status") != "pending" or match.get("invited_actor_id") != subject_id:
            raise SmokeCheckError("created invitation could not be verified in the durable invitation list")
    except SmokeCheckError:
        if invitation_id:
            _revoke_best_effort(opener, base_url, tenant_id, str(invitation_id), admin_token)
        raise

    return (
        "PASS: staging health/readiness, authenticated invitation creation, no-store headers, "
        "token-free response, delivery acceptance, and durable invitation listing all verified.\n"
        f"Delivery event ID for correlation: {event_id}\n"
        "LIMIT: delivery acceptance is not proof of mailbox receipt or successful subject-bound redemption."
    )


def main() -> int:
    try:
        print(run_check())
        return 0
    except SmokeCheckError as exc:
        print(f"STAGING CHECK FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
