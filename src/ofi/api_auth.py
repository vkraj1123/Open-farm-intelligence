"""Fail-closed bearer-token verification for trusted OFI API boundaries.

HS256 remains the default for compatibility with existing deployments. An opt-in
OIDC/JWKS mode validates RS256 access tokens issued by an external identity
provider. OFI validates identity; it does not issue tokens or proof membership
itself. Tenant routes additionally require the authoritative OFI membership
registry to approve the actor's current tenant membership.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import time
from functools import lru_cache
from urllib.parse import urlsplit
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


def _token_identity(
    claims: dict[str, Any],
    *,
    role_claim: str = "role",
    tenant_claim: str = "tenant_id",
) -> ActorIdentity:
    """Validate OFI identity/time claims after signature and issuer checks."""
    actor_id = claims.get("sub")
    role = claims.get(role_claim)
    tenant_id = claims.get(tenant_claim)
    if tenant_id is not None:
        if (
            not isinstance(tenant_id, str)
            or not tenant_id.strip()
            or len(tenant_id) > 512
        ):
            raise ValueError("invalid tenant claim")
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


def _identity_provider_configuration() -> tuple[str, str, str, str, str]:
    jwks_url = os.getenv("OFI_AUTH_JWKS_URL", "").strip()
    issuer = os.getenv("OFI_AUTH_ISSUER", "").strip()
    audience = os.getenv("OFI_AUTH_AUDIENCE", "").strip()
    role_claim = os.getenv("OFI_AUTH_ROLE_CLAIM", "role").strip()
    tenant_claim = os.getenv("OFI_AUTH_TENANT_CLAIM", "tenant_id").strip()
    parsed = urlsplit(jwks_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not issuer
        or not audience
        or not role_claim
        or not tenant_claim
    ):
        raise HTTPException(
            status_code=503,
            detail="OIDC/JWKS authentication is not configured correctly",
        )
    return jwks_url, issuer, audience, role_claim, tenant_claim


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str):
    """Reuse a bounded JWKS client; PyJWKClient refreshes keys when a kid is unknown."""
    try:
        import jwt
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="OIDC/JWKS authentication requires installing the auth extra",
        ) from exc
    return jwt.PyJWKClient(
        jwks_url,
        cache_jwk_set=True,
        lifespan=300,
        timeout=5,
    )


def _verify_hs256_bearer_token(token: str) -> ActorIdentity:
    secret, expected_issuer, expected_audience = _auth_configuration()
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
        return _token_identity(claims)
    except HTTPException:
        raise
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, OverflowError):
        raise _unauthorized() from None


def _verify_oidc_bearer_token(token: str) -> ActorIdentity:
    jwks_url, expected_issuer, expected_audience, role_claim, tenant_claim = (
        _identity_provider_configuration()
    )
    try:
        import jwt
        from jwt.exceptions import (
            InvalidTokenError,
            PyJWKClientConnectionError,
            PyJWKClientError,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="OIDC/JWKS authentication requires installing the auth extra",
        ) from exc

    try:
        header = jwt.get_unverified_header(token)
        # An explicit allow-list prevents algorithm negotiation/downgrade.
        if header.get("alg") != "RS256" or header.get("typ", "JWT") != "JWT":
            raise ValueError("unsupported OIDC JWT header")
        if not isinstance(header.get("kid"), str) or not header["kid"].strip():
            raise ValueError("OIDC JWT requires a key ID")

        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=expected_audience,
            issuer=expected_issuer,
            options={
                "require": ["exp", "iat", "iss", "sub", "aud"],
                "verify_signature": True,
                "verify_exp": True,
                # OFI applies its explicit 60-second issued-at future bound below.
                "verify_iat": False,
                "verify_nbf": True,
            },
            leeway=0,
        )
        if not isinstance(claims, dict):
            raise ValueError("invalid OIDC claims")
        return _token_identity(
            claims,
            role_claim=role_claim,
            tenant_claim=tenant_claim,
        )
    except PyJWKClientConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail="identity provider signing keys are unavailable",
        ) from exc
    except (PyJWKClientError, InvalidTokenError, ValueError, TypeError, OverflowError):
        raise _unauthorized() from None


def verify_bearer_token(token: str) -> ActorIdentity:
    """Verify the configured token profile; misconfiguration always fails closed."""
    if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
        raise _unauthorized()
    mode = os.getenv("OFI_AUTH_MODE", "hs256").strip().lower()
    if mode == "hs256":
        return _verify_hs256_bearer_token(token)
    if mode == "oidc_jwks":
        return _verify_oidc_bearer_token(token)
    raise HTTPException(
        status_code=503,
        detail="bearer-token authentication mode is invalid",
    )


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
