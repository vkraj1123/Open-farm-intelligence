"""Security and ownership tests for the authenticated consent API."""
import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

import ofi.api as api
from ofi.services.consent_repository import InMemoryConsentRepository


SECRET = "unit-test-secret-with-more-than-32-bytes"


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def make_token(
    actor_id="farmer-1",
    role="farmer",
    *,
    secret=SECRET,
    issuer="ofi-test-issuer",
    audience="ofi-api-test",
    expires_in=3600,
    issued_at=None,
    algorithm="HS256",
):
    now = time.time()
    claims = {
        "sub": actor_id,
        "role": role,
        "iss": issuer,
        "aud": audience,
        "iat": now if issued_at is None else issued_at,
        "exp": now + expires_in,
    }
    header = {"alg": algorithm, "typ": "JWT"}
    encoded_header = b64url(json.dumps(header, separators=(",", ":")).encode())
    encoded_claims = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_claims}.{b64url(signature)}"


@pytest.fixture
def consent_client(monkeypatch):
    monkeypatch.setenv("OFI_AUTH_HMAC_SECRET", SECRET)
    monkeypatch.setenv("OFI_AUTH_ISSUER", "ofi-test-issuer")
    monkeypatch.setenv("OFI_AUTH_AUDIENCE", "ofi-api-test")
    store = InMemoryConsentRepository()
    monkeypatch.setattr(api, "consent_repository", store)
    with TestClient(api.app) as client:
        yield client, store


def headers(token=None):
    return {"Authorization": f"Bearer {token}"} if token else {}


def valid_request(**overrides):
    body = {
        "purpose": "share a soil-test result with an extension service",
        "scopes": ["soil_test", "kvk_referral"],
        "affirmative": True,
    }
    body.update(overrides)
    return body


def test_consent_api_requires_bearer_authentication(consent_client):
    client, _ = consent_client
    response = client.post("/consents", json=valid_request())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_consent_lifecycle_uses_authenticated_subject_and_is_owner_bound(consent_client):
    client, store = consent_client
    token = make_token(actor_id="farmer-1")
    created = client.post(
        "/consents",
        headers=headers(token),
        json=valid_request(),
    )
    assert created.status_code == 201
    grant = created.json()
    assert grant["actor_id"] == "farmer-1"
    assert grant["scopes"] == ["soil_test", "kvk_referral"]
    assert grant["revoked_at"] is None

    consent_id = grant["consent_id"]
    fetched = client.get(f"/consents/{consent_id}", headers=headers(token))
    assert fetched.status_code == 200
    assert fetched.json()["consent_id"] == consent_id

    other_actor = headers(make_token(actor_id="farmer-2"))
    assert client.get(f"/consents/{consent_id}", headers=other_actor).status_code == 404
    forbidden_revoke = client.delete(f"/consents/{consent_id}", headers=other_actor)
    assert forbidden_revoke.status_code == 404
    assert store.get(consent_id).revoked_at is None

    revoked = client.delete(f"/consents/{consent_id}", headers=headers(token))
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert not store.get(consent_id).active()


def test_consent_requires_explicit_affirmative_action_and_unique_scopes(consent_client):
    client, _ = consent_client
    token_headers = headers(make_token())
    no_confirmation = valid_request(affirmative=False)
    assert client.post("/consents", headers=token_headers, json=no_confirmation).status_code == 422

    duplicate_scopes = valid_request(scopes=["soil_test", " soil_test "])
    assert client.post("/consents", headers=token_headers, json=duplicate_scopes).status_code == 422


def test_consent_cannot_be_created_for_a_body_supplied_actor(consent_client):
    client, _ = consent_client
    response = client.post(
        "/consents",
        headers=headers(make_token(actor_id="verified-farmer")),
        json=valid_request(actor_id="victim"),
    )
    assert response.status_code == 422


def test_only_farmer_cultivator_and_owner_roles_manage_consent(consent_client):
    client, _ = consent_client
    agent = headers(make_token(actor_id="agent-1", role="agent"))
    response = client.post("/consents", headers=agent, json=valid_request())
    assert response.status_code == 403


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        make_token(secret="wrong-secret-not-the-configured-secret"),
        make_token(issuer="untrusted-issuer"),
        make_token(audience="other-api"),
        make_token(expires_in=-1),
        make_token(algorithm="none"),
    ],
)
def test_invalid_bearer_tokens_fail_closed(consent_client, token):
    client, _ = consent_client
    response = client.post(
        "/consents",
        headers=headers(token),
        json=valid_request(),
    )
    assert response.status_code == 401


def test_missing_auth_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("OFI_AUTH_HMAC_SECRET", raising=False)
    monkeypatch.setenv("OFI_AUTH_ISSUER", "ofi-test-issuer")
    monkeypatch.setenv("OFI_AUTH_AUDIENCE", "ofi-api-test")
    with TestClient(api.app) as client:
        response = client.post(
            "/consents",
            headers=headers(make_token()),
            json=valid_request(),
        )
    assert response.status_code == 503
