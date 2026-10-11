"""Fail-closed HS256 bearer-token authentication for trusted API boundaries.

Tokens must be minted by a trusted issuer that holds OFI_AUTH_HMAC_SECRET.
Resource and consent routes require a signed tenant_id claim; the issuer must
authorize the actor's membership before issuing a token for that tenant.
This module validates identity claims; it does not issue tokens or replace an
OIDC identity provider. Keep the secret in a deployment secret manager.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import time
from typing import Any

from fastapi import Header, HTTPException

from ofi.services.execution_gateway import ActorIdentity


_ALLOWED_ROLES = {
    "farmer",
    "cultivator",
    "owner",
    "agent",
    "service_provider",
    "system",
}
_MAX_TOKEN_BYTES = 16 * 1024
_MAX_TOKEN_LIFETIME_SECONDS = 24 * 60 * 60


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_segment(segment: str) -> bytes:
    if not segment or any(ch.isspace() for ch in segment):
        raise ValueError("invalid JWT segment")
    padded = segment + ("=" * ((4 - len(segment) % 4) % 4))
    return base64.b64decode(padded, altchars=b"-_", validate=True)


def _number_claim(claims: dict[str, Any], name: str) -> float:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {name} claim")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"invalid {name} claim")
    return result


def _auth_configuration() -> tuple[bytes, str, str]:
    secret = os.getenv("OFI_AUTH_HMAC_SECRET", "")
    issuer = os.getenv("OFI_AUTH_ISSUER", "").strip()
    audience = os.getenv("OFI_AUTH_AUDIENCE", "").strip()
    if len(secret.encode("utf-8")) < 32 or not issuer or not audience:
        raise HTTPException(
            status_code=503,
            detail="authenticated API routes are not configured",
        )
    return secret.encode("utf-8"), issuer, audience


def verify_bearer_token(token: str) -> ActorIdentity:
    """Verify signature, issuer, audience and bounded identity/time claims."""
    secret, expected_issuer, expected_audience = _auth_configuration()
    if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
        raise _unauthorized()

    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("malformed JWT")
        header_bytes = _decode_segment(parts[0])
        payload_bytes = _decode_segment(parts[1])
        signature = _decode_segment(parts[2])
        header = json.loads(header_bytes)
        claims = json.loads(payload_bytes)
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise ValueError("invalid JWT JSON")
        if header.get("alg") != "HS256":
            raise ValueError("unsupported JWT algorithm")
        if header.get("typ", "JWT") != "JWT":
            raise ValueError("unsupported JWT type")

        expected_signature = hmac.new(
            secret,
            f"{parts[0]}.{parts[1]}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected_signature, signature):
            raise ValueError("invalid JWT signature")

        if claims.get("iss") != expected_issuer:
            raise ValueError("invalid JWT issuer")
        audience = claims.get("aud")
        if isinstance(audience, str):
            audience_matches = audience == expected_audience
        elif isinstance(audience, list):
            audience_matches = (
                all(isinstance(item, str) for item in audience)
                and expected_audience in audience
            )
        else:
            audience_matches = False
        if not audience_matches:
            raise ValueError("invalid JWT audience")

        actor_id = claims.get("sub")
        role = claims.get("role")
        tenant_id = claims.get("tenant_id")
        if tenant_id is not None:
            if (
                not isinstance(tenant_id, str)
                or not tenant_id.strip()
                or len(tenant_id) > 512
            ):
                raise ValueError("invalid tenant_id claim")
            tenant_id = tenant_id.strip()
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 512:
            raise ValueError("invalid JWT subject")
        if not isinstance(role, str) or role not in _ALLOWED_ROLES:
            raise ValueError("invalid JWT role")

        now = time.time()
        issued_at = _number_claim(claims, "iat")
        expires_at = _number_claim(claims, "exp")
        if issued_at > now + 60:
            raise ValueError("JWT issued in the future")
        if expires_at <= now or expires_at <= issued_at:
            raise ValueError("JWT expired")
        if expires_at - issued_at > _MAX_TOKEN_LIFETIME_SECONDS:
            raise ValueError("JWT lifetime exceeds the 24-hour limit")
        if "nbf" in claims and _number_claim(claims, "nbf") > now:
            raise ValueError("JWT is not active yet")

        return ActorIdentity(
            actor_id=actor_id.strip(),
            role=role,
            authenticated=True,
            tenant_id=tenant_id,
        )
    except HTTPException:
        raise
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, OverflowError):
        raise _unauthorized() from None


def require_authenticated_actor(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ActorIdentity:
    """FastAPI dependency resolving a verified bearer token to an actor."""
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    return verify_bearer_token(token.strip())
