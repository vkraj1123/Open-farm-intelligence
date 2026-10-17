"""End-to-end OIDC/JWKS bearer-to-membership acceptance tests.

These tests sign synthetic RS256 tokens and stub only the external JWKS lookup.
They exercise the real verifier, FastAPI auth dependency, and in-memory current
membership registry; they do not call Auth0 or change deployment settings.
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from ofi import api, api_auth
from ofi.services.case_manager import CaseManager
from ofi.services.evidence_comparison_ledger import InMemoryEvidenceComparisonLedger
from ofi.services.tenant_membership_repository import InMemoryTenantMembershipRepository

ISSUER = "https://identity.example.test/"
JWKS_URL = "https://identity.example.test/.well-known/jwks.json"
AUDIENCE = "ofi-api"
ROLE_CLAIM = "https://claims.example.org/ofi/role"
TENANT_CLAIM = "https://claims.example.org/ofi/tenant_id"
ACTOR_ID = "google-oauth2|ofi-test-user-17"
TENANT_ID = "tenant-a"


@pytest.fixture
def oidc_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def oidc_membership_client(monkeypatch, oidc_keys):
    private, public = oidc_keys
    monkeypatch.setenv("OFI_ENV", "test")
    monkeypatch.setenv("OFI_RESOURCE_AUTH_ENABLED", "true")
    monkeypatch.setenv("OFI_TENANT_MEMBERSHIP_ENFORCEMENT", "required")
    monkeypatch.setenv("OFI_AUTH_MODE", "oidc_jwks")
    monkeypatch.setenv("OFI_AUTH_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("OFI_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("OFI_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("OFI_AUTH_ROLE_CLAIM", ROLE_CLAIM)
    monkeypatch.setenv("OFI_AUTH_TENANT_CLAIM", TENANT_CLAIM)

    # Simulate the public key returned by the verified issuer's JWKS endpoint.
    # The actual JWT signature verification and OFI request authorization remain real.
    monkeypatch.setattr(
        api_auth,
        "_jwks_client",
        lambda _url: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public)
        ),
    )
    repository = InMemoryTenantMembershipRepository()
    monkeypatch.setattr(api, "case_manager", CaseManager())
    monkeypatch.setattr(api, "evidence_comparison_ledger", InMemoryEvidenceComparisonLedger())
    monkeypatch.setattr(api, "tenant_membership_repository", repository)

    with TestClient(api.app) as client:
        yield client, repository, private


def oidc_token(
    private_key,
    *,
    subject=ACTOR_ID,
    role="farmer",
    tenant_id=TENANT_ID,
    include_role=True,
    include_tenant=True,
    issuer=ISSUER,
    audience=AUDIENCE,
):
    now = int(time.time())
    claims = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + 900,
    }
    if include_role:
        claims[ROLE_CLAIM] = role
    if include_tenant:
        claims[TENANT_CLAIM] = tenant_id
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "ofi-synthetic-test-key", "typ": "JWT"},
    )


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_rs256_namespaced_claims_and_active_membership_pass_request_gate(
    oidc_membership_client,
):
    client, repository, private = oidc_membership_client
    repository.grant(TENANT_ID, ACTOR_ID, "member", granted_by="trusted-test-provisioner")
    response = client.get(
        "/cases/does-not-exist",
        headers=bearer(oidc_token(private)),
    )
    # 404 confirms authentication and active membership passed; only the case is absent.
    assert response.status_code == 404
    assert response.json()["detail"] == "case not found"


def test_valid_signed_tenant_claim_does_not_replace_membership(
    oidc_membership_client,
):
    client, _repository, private = oidc_membership_client
    response = client.get(
        "/cases/does-not-exist",
        headers=bearer(oidc_token(private)),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "active tenant membership is required"


@pytest.mark.parametrize("status", ["suspended", "revoked"])
def test_suspended_or_revoked_membership_is_denied_even_with_valid_token(
    oidc_membership_client, status,
):
    client, repository, private = oidc_membership_client
    repository.grant(TENANT_ID, ACTOR_ID, "member", granted_by="trusted-test-provisioner")
    repository.update(
        TENANT_ID,
        ACTOR_ID,
        "member",
        status,
        changed_by="trusted-test-provisioner",
    )
    response = client.get(
        "/cases/does-not-exist",
        headers=bearer(oidc_token(private)),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "active tenant membership is required"


def test_token_for_different_tenant_does_not_reuse_another_tenant_membership(
    oidc_membership_client,
):
    client, repository, private = oidc_membership_client
    repository.grant(TENANT_ID, ACTOR_ID, "member", granted_by="trusted-test-provisioner")
    response = client.get(
        "/cases/does-not-exist",
        headers=bearer(oidc_token(private, tenant_id="tenant-b")),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "active tenant membership is required"


def test_missing_trusted_role_claim_is_rejected_before_resource_access(
    oidc_membership_client,
):
    client, repository, private = oidc_membership_client
    repository.grant(TENANT_ID, ACTOR_ID, "member", granted_by="trusted-test-provisioner")
    response = client.get(
        "/cases/does-not-exist",
        headers=bearer(oidc_token(private, include_role=False)),
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid bearer token"
