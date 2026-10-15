"""Tests for fail-closed HS256 and OIDC/JWKS bearer verification."""
import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from jwt.exceptions import PyJWKClientConnectionError

from ofi import api_auth

SECRET = "unit-test-secret-with-at-least-32-bytes"
ISSUER = "https://identity.example.test/"
AUDIENCE = "ofi-api"
JWKS_URL = "https://identity.example.test/.well-known/jwks.json"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def hs256_token(claims: dict, secret: str = SECRET) -> str:
    header = _b64(b'{"alg":"HS256","typ":"JWT"}')
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}"


@pytest.fixture
def rsa_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private, private_pem, private.public_key()


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("OFI_AUTH_HMAC_SECRET", SECRET)
    monkeypatch.setenv("OFI_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("OFI_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("OFI_AUTH_MODE", "hs256")


@pytest.fixture
def oidc_env(monkeypatch):
    monkeypatch.setenv("OFI_AUTH_MODE", "oidc_jwks")
    monkeypatch.setenv("OFI_AUTH_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("OFI_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("OFI_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("OFI_AUTH_ROLE_CLAIM", "role")
    monkeypatch.setenv("OFI_AUTH_TENANT_CLAIM", "tenant_id")


def claims(**updates):
    now = int(time.time())
    base = {
        "sub": "actor-17",
        "role": "farmer",
        "tenant_id": "tenant-a",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 900,
    }
    base.update(updates)
    return base


def oidc_token(private_pem: bytes, claim_values: dict | None = None, *, algorithm="RS256"):
    return jwt.encode(
        claims(**(claim_values or {})),
        private_pem,
        algorithm=algorithm,
        headers={"kid": "test-key", "typ": "JWT"},
    )


def _stub_jwks(monkeypatch, public_key):
    monkeypatch.setattr(
        api_auth,
        "_jwks_client",
        lambda _url: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public_key)
        ),
    )


def test_hs256_compatibility_and_tenant_claim(auth_env):
    token = hs256_token(claims())
    actor = api_auth.verify_bearer_token(token)
    assert actor.actor_id == "actor-17"
    assert actor.role == "farmer"
    assert actor.tenant_id == "tenant-a"
    assert actor.authenticated is True


def test_hs256_bad_signature_is_rejected(auth_env):
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token(hs256_token(claims(), "a-different-secret-long-enough"))
    assert error.value.status_code == 401


def test_oidc_rs256_token_is_verified_and_claim_mapping_is_configurable(
    oidc_env, rsa_keys, monkeypatch
):
    _private, private_pem, public_key = rsa_keys
    monkeypatch.setenv("OFI_AUTH_ROLE_CLAIM", "ofi_role")
    monkeypatch.setenv("OFI_AUTH_TENANT_CLAIM", "organization_id")
    token = jwt.encode(
        claims(ofi_role="cultivator", organization_id="org-9"),
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key", "typ": "JWT"},
    )
    _stub_jwks(monkeypatch, public_key)

    actor = api_auth.verify_bearer_token(token)
    assert actor.actor_id == "actor-17"
    assert actor.role == "cultivator"
    assert actor.tenant_id == "org-9"


def test_oidc_rejects_wrong_signature(oidc_env, rsa_keys, monkeypatch):
    _private, private_pem, _public_key = rsa_keys
    other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_public = other_private.public_key()
    token = oidc_token(private_pem)
    _stub_jwks(monkeypatch, other_public)
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token(token)
    assert error.value.status_code == 401


def test_oidc_rejects_hs256_algorithm_even_with_valid_secret(
    oidc_env, rsa_keys, monkeypatch
):
    _private, private_pem, public_key = rsa_keys
    _stub_jwks(monkeypatch, public_key)
    # OIDC mode permits RS256 only, never an algorithm selected by the token.
    token = jwt.encode(
        claims(),
        SECRET,
        algorithm="HS256",
        headers={"kid": "test-key", "typ": "JWT"},
    )
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token(token)
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://attacker.example/"},
        {"aud": "another-api"},
        {"role": "platform_superuser"},
        {"tenant_id": ""},
        {"exp": int(time.time()) - 5},
        {"exp": int(time.time()) + 90_000},
        {"nbf": int(time.time()) + 300},
    ],
)
def test_oidc_rejects_invalid_issuer_audience_lifetime_and_identity_claims(
    oidc_env, rsa_keys, monkeypatch, changes
):
    _private, private_pem, public_key = rsa_keys
    _stub_jwks(monkeypatch, public_key)
    token = oidc_token(private_pem, changes)
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token(token)
    assert error.value.status_code == 401


def test_oidc_key_service_failure_is_unavailable_not_bad_credentials(
    oidc_env, rsa_keys, monkeypatch
):
    _private, private_pem, _public_key = rsa_keys

    def unavailable(_token):
        raise PyJWKClientConnectionError("private provider detail")

    monkeypatch.setattr(
        api_auth,
        "_jwks_client",
        lambda _url: SimpleNamespace(get_signing_key_from_jwt=unavailable),
    )
    token = oidc_token(private_pem)
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token(token)
    assert error.value.status_code == 503
    assert error.value.detail == "identity provider signing keys are unavailable"


def test_oidc_jwks_url_requires_https(oidc_env, monkeypatch):
    monkeypatch.setenv("OFI_AUTH_JWKS_URL", "http://identity.example.test/jwks")
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token("not-a-jwt")
    assert error.value.status_code == 401  # size/shape validation precedes config
    valid_shape_token = hs256_token(claims())
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token(valid_shape_token)
    assert error.value.status_code == 503


def test_unknown_mode_and_missing_oidc_configuration_fail_closed(oidc_env, monkeypatch):
    monkeypatch.setenv("OFI_AUTH_MODE", "untrusted_mode")
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token(hs256_token(claims()))
    assert error.value.status_code == 503

    monkeypatch.setenv("OFI_AUTH_MODE", "oidc_jwks")
    monkeypatch.delenv("OFI_AUTH_JWKS_URL")
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token(hs256_token(claims()))
    assert error.value.status_code == 503


def test_hs256_boolean_time_claim_is_rejected(auth_env):
    with pytest.raises(HTTPException) as error:
        api_auth.verify_bearer_token(hs256_token(claims(iat=True)))
    assert error.value.status_code == 401
