"""Tenant membership authorization and administration API regressions."""
import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import ofi.api as api
from ofi.services.case_manager import CaseManager
from ofi.services.evidence_comparison_ledger import InMemoryEvidenceComparisonLedger
from ofi.services.tenant_membership_repository import InMemoryTenantMembershipRepository

SECRET = "tenant-membership-api-test-secret-over-32-bytes"
ISSUER = "ofi-membership-test-issuer"
AUDIENCE = "ofi-membership-test-api"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def token(actor_id: str, *, role="farmer", tenant_id="tenant-a") -> str:
    now = time.time()
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({
        "sub": actor_id, "role": role, "iss": ISSUER, "aud": AUDIENCE,
        "iat": now, "exp": now + 3600, "tenant_id": tenant_id,
    }, separators=(",", ":")).encode())
    signed = f"{header}.{claims}".encode("ascii")
    signature = hmac.new(SECRET.encode(), signed, hashlib.sha256).digest()
    return f"{header}.{claims}.{_b64(signature)}"


def headers(actor_id: str, *, role="farmer", tenant_id="tenant-a"):
    return {"Authorization": f"Bearer {token(actor_id, role=role, tenant_id=tenant_id)}"}


@pytest.fixture
def membership_client(monkeypatch):
    monkeypatch.setenv("OFI_ENV", "test")
    monkeypatch.setenv("OFI_RESOURCE_AUTH_ENABLED", "true")
    monkeypatch.setenv("OFI_TENANT_MEMBERSHIP_ENFORCEMENT", "required")
    monkeypatch.setenv("OFI_AUTH_HMAC_SECRET", SECRET)
    monkeypatch.setenv("OFI_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("OFI_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(api, "case_manager", CaseManager())
    monkeypatch.setattr(api, "evidence_comparison_ledger", InMemoryEvidenceComparisonLedger())
    monkeypatch.setattr(api, "tenant_membership_repository", InMemoryTenantMembershipRepository())
    with TestClient(api.app) as client:
        yield client


def test_signed_tenant_claim_without_current_membership_is_denied(membership_client):
    response = membership_client.get(
        "/cases/not-present",
        headers=headers("unregistered-actor", tenant_id="tenant-a"),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "active tenant membership is required"

    consent = membership_client.post(
        "/consents",
        headers=headers("unregistered-actor", tenant_id="tenant-a"),
        json={
            "purpose": "soil testing",
            "scopes": ["soil_test"],
            "affirmative": True,
        },
    )
    assert consent.status_code == 403


def test_platform_bootstrap_tenant_admin_and_audited_member_lifecycle(membership_client):
    client = membership_client
    bootstrap = client.post(
        "/tenants/tenant-a/memberships",
        headers=headers("platform-root", role="system", tenant_id="platform"),
        json={"actor_id": "alice", "membership_role": "tenant_admin"},
    )
    assert bootstrap.status_code == 201
    assert bootstrap.json()["status"] == "active"
    assert bootstrap.json()["membership_role"] == "tenant_admin"

    assert client.get(
        "/cases/not-present", headers=headers("alice", tenant_id="tenant-a")
    ).status_code == 404  # Membership passed; the case itself does not exist.

    granted = client.post(
        "/tenants/tenant-a/memberships",
        headers=headers("alice", tenant_id="tenant-a"),
        json={"actor_id": "bob", "membership_role": "member"},
    )
    assert granted.status_code == 201
    assert client.get(
        "/cases/not-present", headers=headers("bob", tenant_id="tenant-a")
    ).status_code == 404

    forbidden_promotion = client.post(
        "/tenants/tenant-a/memberships",
        headers=headers("alice", tenant_id="tenant-a"),
        json={"actor_id": "bob", "membership_role": "tenant_admin"},
    )
    assert forbidden_promotion.status_code == 403
    assert client.get(
        "/tenants/tenant-b/memberships", headers=headers("alice", tenant_id="tenant-a")
    ).status_code == 404

    suspended = client.patch(
        "/tenants/tenant-a/memberships/bob",
        headers=headers("alice", tenant_id="tenant-a"),
        json={"membership_role": "member", "status": "suspended"},
    )
    assert suspended.status_code == 200
    assert client.get(
        "/cases/not-present", headers=headers("bob", tenant_id="tenant-a")
    ).status_code == 403

    events = client.get(
        "/tenants/tenant-a/membership-events",
        headers=headers("alice", tenant_id="tenant-a"),
    )
    assert events.status_code == 200
    assert [e["action"] for e in events.json()["events"]] == [
        "suspended", "created", "created"
    ]


def test_tenant_admin_cannot_demote_another_tenant_admin_via_grant(membership_client):
    client = membership_client
    repository = api.tenant_membership_repository
    repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    repository.grant("tenant-a", "carol", "tenant_admin", granted_by="platform-root")
    response = client.post(
        "/tenants/tenant-a/memberships",
        headers=headers("alice", tenant_id="tenant-a"),
        json={"actor_id": "carol", "membership_role": "member"},
    )
    assert response.status_code == 403
    assert repository.get_active("tenant-a", "carol").membership_role == "tenant_admin"


def test_last_active_tenant_admin_cannot_be_demoted_or_revoked(membership_client):
    client = membership_client
    repository = api.tenant_membership_repository
    repository.grant("tenant-a", "sole-admin", "tenant_admin", granted_by="platform-root")
    # Tenant admins cannot manage tenant-admin roles themselves.
    result = client.patch(
        "/tenants/tenant-a/memberships/sole-admin",
        headers=headers("sole-admin", tenant_id="tenant-a"),
        json={"membership_role": "member", "status": "revoked"},
    )
    assert result.status_code == 403
    # Platform system can request the change, but the registry protects the last admin.
    result = client.patch(
        "/tenants/tenant-a/memberships/sole-admin",
        headers=headers("platform-root", role="system", tenant_id="platform"),
        json={"membership_role": "member", "status": "revoked"},
    )
    assert result.status_code == 409
    assert repository.get_active("tenant-a", "sole-admin").membership_role == "tenant_admin"


def test_membership_enforcement_cannot_be_disabled_outside_test_mode(membership_client, monkeypatch):
    monkeypatch.setenv("OFI_ENV", "production")
    monkeypatch.setenv("OFI_TENANT_MEMBERSHIP_ENFORCEMENT", "disabled")
    response = membership_client.get(
        "/cases/not-present", headers=headers("any-actor", tenant_id="tenant-a")
    )
    assert response.status_code == 503



def test_resource_authentication_cannot_be_disabled_outside_test_mode(membership_client, monkeypatch):
    monkeypatch.setenv("OFI_ENV", "production")
    monkeypatch.setenv("OFI_RESOURCE_AUTH_ENABLED", "false")
    response = membership_client.get("/cases/not-present")
    assert response.status_code == 503

def _create_invitation(client, actor_id="alice", target="bob", tenant_id="tenant-a"):
    return client.post(
        f"/tenants/{tenant_id}/invitations",
        headers=headers(actor_id, tenant_id=tenant_id),
        json={"actor_id": target, "expires_in_hours": 24},
    )


def test_invitation_is_subject_bound_single_use_and_audited(membership_client):
    client = membership_client
    repository = api.tenant_membership_repository
    repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    created = _create_invitation(client)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store, private"
    assert created.headers["pragma"] == "no-cache"
    body = created.json()
    invitation_id = body["invitation"]["invitation_id"]
    secret = body["invitation_token"]
    assert len(secret) >= 40
    assert "token_sha256" not in body["invitation"]
    token_hash = hashlib.sha256(secret.encode()).hexdigest()
    assert token_hash in repository._invitations
    assert secret not in repr(repository._invitations)

    listed = client.get("/tenants/tenant-a/invitations", headers=headers("alice", tenant_id="tenant-a"))
    assert listed.status_code == 200
    assert listed.json()["invitations"][0]["status"] == "pending"
    assert secret not in listed.text
    assert token_hash not in listed.text

    wrong_subject = client.post(
        "/tenants/tenant-a/invitations/redeem",
        headers=headers("mallory", tenant_id="tenant-a"),
        json={"invitation_token": secret},
    )
    assert wrong_subject.status_code == 400
    assert repository._invitations[token_hash].status == "pending"

    redeemed = client.post(
        "/tenants/tenant-a/invitations/redeem",
        headers=headers("bob", tenant_id="tenant-a"),
        json={"invitation_token": secret},
    )
    assert redeemed.status_code == 200
    assert redeemed.json()["actor_id"] == "bob"
    assert redeemed.json()["membership_role"] == "member"
    assert client.get("/cases/not-present", headers=headers("bob", tenant_id="tenant-a")).status_code == 404

    replay = client.post(
        "/tenants/tenant-a/invitations/redeem",
        headers=headers("bob", tenant_id="tenant-a"),
        json={"invitation_token": secret},
    )
    assert replay.status_code == 400
    events = client.get("/tenants/tenant-a/invitation-events", headers=headers("alice", tenant_id="tenant-a"))
    assert [e["action"] for e in events.json()["events"]] == ["redeemed", "created"]
    assert events.json()["events"][0]["invitation_id"] == invitation_id


def test_invitation_cannot_be_redeemed_from_another_tenant_path(membership_client):
    client = membership_client
    api.tenant_membership_repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    created = _create_invitation(client)
    secret = created.json()["invitation_token"]
    wrong_tenant = client.post(
        "/tenants/tenant-b/invitations/redeem",
        headers=headers("bob", tenant_id="tenant-a"),
        json={"invitation_token": secret},
    )
    assert wrong_tenant.status_code == 400
    assert client.post(
        "/tenants/tenant-a/invitations/redeem",
        headers=headers("bob", tenant_id="tenant-a"),
        json={"invitation_token": secret},
    ).status_code == 200


def test_invitation_revocation_blocks_redemption_and_is_audited(membership_client):
    client = membership_client
    api.tenant_membership_repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    created = _create_invitation(client)
    invitation = created.json()["invitation"]
    secret = created.json()["invitation_token"]
    revoked = client.post(
        f"/tenants/tenant-a/invitations/{invitation['invitation_id']}/revoke",
        headers=headers("alice", tenant_id="tenant-a"),
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    rejected = client.post(
        "/tenants/tenant-a/invitations/redeem",
        headers=headers("bob", tenant_id="tenant-a"),
        json={"invitation_token": secret},
    )
    assert rejected.status_code == 400
    events = client.get("/tenants/tenant-a/invitation-events", headers=headers("alice", tenant_id="tenant-a"))
    assert [e["action"] for e in events.json()["events"]] == ["revoked", "created"]


def test_expired_invitation_is_rejected_and_audited(membership_client):
    client = membership_client
    repository = api.tenant_membership_repository
    repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    created = _create_invitation(client)
    secret = created.json()["invitation_token"]
    token_hash = hashlib.sha256(secret.encode()).hexdigest()
    old = repository._invitations[token_hash]
    now = datetime.now(timezone.utc)
    repository._invitations[token_hash] = replace(
        old, created_at=now - timedelta(hours=2), expires_at=now - timedelta(hours=1)
    )
    response = client.post(
        "/tenants/tenant-a/invitations/redeem",
        headers=headers("bob", tenant_id="tenant-a"),
        json={"invitation_token": secret},
    )
    assert response.status_code == 400
    assert repository._invitations[token_hash].status == "expired"
    events = client.get("/tenants/tenant-a/invitation-events", headers=headers("alice", tenant_id="tenant-a"))
    assert [e["action"] for e in events.json()["events"]] == ["expired", "created"]


def test_invitation_creation_requires_admin_and_rejects_active_member_or_duplicate(membership_client):
    client = membership_client
    denied = client.post(
        "/tenants/tenant-a/invitations",
        headers=headers("not-admin", tenant_id="tenant-a"),
        json={"actor_id": "bob"},
    )
    assert denied.status_code == 403
    repository = api.tenant_membership_repository
    repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    first = _create_invitation(client, target="bob")
    assert first.status_code == 201
    duplicate = _create_invitation(client, target="bob")
    assert duplicate.status_code == 409
    repository.grant("tenant-a", "carol", "member", granted_by="alice")
    active = _create_invitation(client, target="carol")
    assert active.status_code == 409
    admin_role_attempt = client.post(
        "/tenants/tenant-a/invitations",
        headers=headers("alice", tenant_id="tenant-a"),
        json={"actor_id": "dave", "membership_role": "tenant_admin"},
    )
    assert admin_role_attempt.status_code == 422

def _as_durable_test_repository(monkeypatch, repository):
    """Proxy test memory state so the API's durable-storage guard can be tested around."""
    class DurableRepositoryProxy:
        def __getattr__(self, name):
            return getattr(repository, name)

    monkeypatch.setattr(api, "tenant_membership_repository", DurableRepositoryProxy())


def test_webhook_delivery_does_not_return_token_and_reports_signed_delivery_receipt(
    membership_client, monkeypatch
):
    client = membership_client
    repository = api.tenant_membership_repository
    repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    _as_durable_test_repository(monkeypatch, repository)
    monkeypatch.setenv("OFI_INVITATION_DELIVERY_MODE", "webhook")
    sent = {}

    class FakeWebhook:
        def send_invitation(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(event_id="event-digest-123", status_code=202)

    monkeypatch.setattr(api, "_configured_tenant_invitation_webhook", FakeWebhook)
    response = _create_invitation(client)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    body = response.json()
    assert body["delivery"] == "accepted"
    assert body["delivery_event_id"] == "event-digest-123"
    assert "invitation_token" not in body
    assert sent["tenant_id"] == "tenant-a"
    assert sent["subject_id"] == "bob"
    assert sent["invitation_token"]
    digest = hashlib.sha256(sent["invitation_token"].encode()).hexdigest()
    assert digest in repository._invitations
    assert repository._invitations[digest].status == "pending"


def test_webhook_delivery_failure_revokes_invitation_and_never_returns_token(
    membership_client, monkeypatch
):
    client = membership_client
    repository = api.tenant_membership_repository
    repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    _as_durable_test_repository(monkeypatch, repository)
    monkeypatch.setenv("OFI_INVITATION_DELIVERY_MODE", "webhook")

    class BrokenWebhook:
        def send_invitation(self, **kwargs):
            raise api.InvitationDeliveryError(
                "transport failed; one-time-token-must-not-leak"
            )

    monkeypatch.setattr(api, "_configured_tenant_invitation_webhook", BrokenWebhook)
    response = _create_invitation(client)

    assert response.status_code == 503
    assert "one-time-token-must-not-leak" not in response.text
    pending_or_revoked = list(repository._invitations.items())
    assert len(pending_or_revoked) == 1
    token_hash, invitation = pending_or_revoked[0]
    assert invitation.status == "revoked"
    assert [event["action"] for event in repository.list_invitation_events("tenant-a")] == [
        "revoked", "created"
    ]
    assert token_hash not in response.text


def test_webhook_delivery_requires_valid_configuration_before_creating_invitation(
    membership_client, monkeypatch
):
    client = membership_client
    repository = api.tenant_membership_repository
    repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    monkeypatch.setenv("OFI_INVITATION_DELIVERY_MODE", "webhook")
    monkeypatch.delenv("OFI_INVITATION_DELIVERY_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("OFI_INVITATION_DELIVERY_WEBHOOK_SECRET", raising=False)

    response = _create_invitation(client)

    assert response.status_code == 503
    assert "invitation webhook delivery is not configured" in response.json()["detail"]
    assert repository._invitations == {}


def test_webhook_delivery_is_rejected_for_in_memory_storage(
    membership_client, monkeypatch
):
    client = membership_client
    repository = api.tenant_membership_repository
    repository.grant("tenant-a", "alice", "tenant_admin", granted_by="platform-root")
    monkeypatch.setenv("OFI_INVITATION_DELIVERY_MODE", "webhook")

    class FakeWebhook:
        def send_invitation(self, **kwargs):
            raise AssertionError("webhook must not run without durable storage")

    monkeypatch.setattr(api, "_configured_tenant_invitation_webhook", FakeWebhook)

    response = _create_invitation(client)

    assert response.status_code == 503
    assert "requires durable PostgreSQL storage" in response.json()["detail"]
    assert repository._invitations == {}
