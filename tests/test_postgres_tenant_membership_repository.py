"""PostgreSQL integration checks for durable tenant membership and audit history."""
import os
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from ofi.services.tenant_membership_repository import (
    InvitationUnavailableError,
    LastTenantAdminError,
    PostgresTenantMembershipRepository,
)
from ofi.twin.migrate import apply_schema

DATABASE_URL = os.environ.get("OFI_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires PostgreSQL CI service via OFI_POSTGRES_DSN",
)


def test_postgres_membership_state_is_tenant_scoped_and_audited():
    assert apply_schema(DATABASE_URL) >= 6
    suffix = uuid4().hex
    tenant_a = f"membership-tenant-a-{suffix}"
    tenant_b = f"membership-tenant-b-{suffix}"
    connections = lambda: psycopg.connect(DATABASE_URL)
    repo = PostgresTenantMembershipRepository(connections)

    admin = repo.grant(tenant_a, f"admin-{suffix}", "tenant_admin", granted_by="platform-root")
    member = repo.grant(tenant_a, f"member-{suffix}", "member", granted_by=admin.actor_id)
    other_actor = f"other-{suffix}"
    repo.grant(tenant_b, other_actor, "member", granted_by="platform-root")
    assert repo.get_active(tenant_a, admin.actor_id) == admin
    assert repo.get_active(tenant_a, member.actor_id) == member
    with pytest.raises(KeyError):
        repo.get_active(tenant_a, other_actor)

    suspended = repo.update(
        tenant_a, member.actor_id, "member", "suspended", changed_by=admin.actor_id
    )
    assert suspended.status == "suspended"
    with pytest.raises(KeyError):
        repo.get_active(tenant_a, member.actor_id)
    reactivated = repo.update(
        tenant_a, member.actor_id, "member", "active", changed_by=admin.actor_id
    )
    assert reactivated.status == "active"

    with pytest.raises(LastTenantAdminError):
        repo.update(
            tenant_a, admin.actor_id, "member", "revoked", changed_by="platform-root"
        )
    events = repo.list_events(tenant_a, limit=20)
    assert [item["action"] for item in events][:4] == [
        "reactivated", "suspended", "created", "created"
    ]
    assert all(item["tenant_id"] == tenant_a for item in events)

    event_id = events[0]["event_id"]
    with pytest.raises(psycopg.Error, match="append-only"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "UPDATE ofi_tenant_membership_events SET action='rewritten' WHERE event_id=%s",
                (event_id,),
            )

def test_postgres_invitation_redemption_is_single_use_and_audited():
    assert apply_schema(DATABASE_URL) >= 6
    suffix = uuid4().hex
    tenant_id = f"invite-tenant-{suffix}"
    intended_actor = f"invite-actor-{suffix}"
    token = f"test-secret-{uuid4().hex}-{uuid4().hex}"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    connections = lambda: psycopg.connect(DATABASE_URL)
    repo = PostgresTenantMembershipRepository(connections)
    invitation = repo.create_invitation(
        f"invite-{suffix}", tenant_id, intended_actor, token_hash,
        datetime.now(timezone.utc) + timedelta(hours=1), created_by="tenant-admin",
    )
    with pytest.raises(ValueError, match="invalid or unavailable"):
        repo.redeem_invitation(token_hash, f"wrong-{suffix}", tenant_id=tenant_id)
    assert repo.list_invitations(tenant_id, checked_by="tenant-admin")[0].status == "pending"

    membership = repo.redeem_invitation(token_hash, intended_actor, tenant_id=tenant_id)
    assert membership.actor_id == intended_actor
    assert membership.membership_role == "member"
    assert repo.get_active(tenant_id, intended_actor) == membership
    with pytest.raises(ValueError, match="invalid or unavailable"):
        repo.redeem_invitation(token_hash, intended_actor, tenant_id=tenant_id)

    listed = repo.list_invitations(tenant_id, checked_by="tenant-admin")
    assert listed[0].invitation_id == invitation.invitation_id
    assert listed[0].status == "redeemed"
    assert [event["action"] for event in repo.list_invitation_events(tenant_id)] == ["redeemed", "created"]
    with psycopg.connect(DATABASE_URL) as connection:
        count = connection.execute(
            "SELECT count(*) FROM ofi_tenant_membership_events WHERE tenant_id=%s AND subject_actor_id=%s",
            (tenant_id, intended_actor),
        ).fetchone()[0]
    assert count == 1

def test_postgres_concurrent_redemption_consumes_invitation_exactly_once():
    assert apply_schema(DATABASE_URL) >= 6
    suffix = uuid4().hex
    tenant_id = f"invite-race-tenant-{suffix}"
    intended_actor = f"invite-race-actor-{suffix}"
    raw_token = f"race-secret-{uuid4().hex}-{uuid4().hex}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    repo = PostgresTenantMembershipRepository(lambda: psycopg.connect(DATABASE_URL))
    repo.create_invitation(
        f"invite-race-{suffix}", tenant_id, intended_actor, token_hash,
        datetime.now(timezone.utc) + timedelta(hours=1), created_by="tenant-admin",
    )

    def redeem_once(_):
        try:
            membership = repo.redeem_invitation(token_hash, intended_actor, tenant_id=tenant_id)
            return ("redeemed", membership.actor_id)
        except InvitationUnavailableError:
            return ("unavailable", None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(redeem_once, range(2)))

    assert sorted(outcome for outcome, _actor in outcomes) == ["redeemed", "unavailable"]
    assert sum(actor == intended_actor for _outcome, actor in outcomes) == 1
    assert repo.get_active(tenant_id, intended_actor).membership_role == "member"
    invitation_events = repo.list_invitation_events(tenant_id)
    assert [event["action"] for event in invitation_events] == ["redeemed", "created"]
    with psycopg.connect(DATABASE_URL) as connection:
        membership_rows = connection.execute(
            "SELECT count(*) FROM ofi_tenant_memberships WHERE tenant_id=%s AND actor_id=%s",
            (tenant_id, intended_actor),
        ).fetchone()[0]
        audit_rows = connection.execute(
            "SELECT count(*) FROM ofi_tenant_membership_events WHERE tenant_id=%s AND subject_actor_id=%s",
            (tenant_id, intended_actor),
        ).fetchone()[0]
        stored_digest = connection.execute(
            "SELECT token_sha256 FROM ofi_tenant_invitations WHERE tenant_id=%s AND invited_actor_id=%s",
            (tenant_id, intended_actor),
        ).fetchone()[0]
    assert membership_rows == 1
    assert audit_rows == 1
    assert stored_digest == token_hash
    assert raw_token not in stored_digest
