"""Authoritative tenant membership registry and append-only admin audit trail."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Literal, Protocol

from ofi.services.db_context import connection_scope

MembershipRole = Literal["tenant_admin", "member"]
MembershipStatus = Literal["active", "suspended", "revoked"]
InvitationStatus = Literal["pending", "redeemed", "revoked", "expired"]


class LastTenantAdminError(ValueError):
    """Raised when a mutation would leave a tenant without an active administrator."""


@dataclass(frozen=True)
class TenantMembership:
    tenant_id: str
    actor_id: str
    membership_role: MembershipRole
    status: MembershipStatus
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "actor_id", "created_by", "updated_by"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
                raise ValueError(f"{field_name} must be non-empty and at most 512 characters")
            object.__setattr__(self, field_name, value.strip())
        if self.membership_role not in {"tenant_admin", "member"}:
            raise ValueError("unsupported tenant membership role")
        if self.status not in {"active", "suspended", "revoked"}:
            raise ValueError("unsupported tenant membership status")
        for field_name in ("created_at", "updated_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
            object.__setattr__(self, field_name, value.astimezone(timezone.utc))


class InvitationUnavailableError(ValueError):
    """An invitation token is not redeemable by this identity."""


class InvitationConflictError(ValueError):
    """The invitation conflicts with current membership state."""


@dataclass(frozen=True)
class TenantInvitation:
    invitation_id: str
    tenant_id: str
    invited_actor_id: str
    token_sha256: str
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime
    created_by: str
    redeemed_at: datetime | None = None
    redeemed_by: str | None = None
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    def __post_init__(self) -> None:
        for name in ("invitation_id", "tenant_id", "invited_actor_id", "created_by"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
                raise ValueError(f"{name} must be non-empty and at most 512 characters")
            object.__setattr__(self, name, value.strip())
        if (not isinstance(self.token_sha256, str) or len(self.token_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.token_sha256)):
            raise ValueError("token_sha256 must be a lowercase SHA-256 hex digest")
        if self.status not in {"pending", "redeemed", "revoked", "expired"}:
            raise ValueError("unsupported invitation status")
        for name in ("created_at", "expires_at", "redeemed_at", "revoked_at"):
            value = getattr(self, name)
            if value is None:
                continue
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
            object.__setattr__(self, name, value.astimezone(timezone.utc))
        if self.expires_at <= self.created_at:
            raise ValueError("invitation expiry must follow creation")
        for name in ("redeemed_by", "revoked_by"):
            value = getattr(self, name)
            if value is not None:
                if not value.strip() or len(value.strip()) > 512:
                    raise ValueError(f"{name} must be non-empty and at most 512 characters")
                object.__setattr__(self, name, value.strip())


class TenantMembershipRepository(Protocol):
    def get(self, tenant_id: str, actor_id: str) -> TenantMembership: ...
    def get_active(self, tenant_id: str, actor_id: str) -> TenantMembership: ...
    def list_for_tenant(self, tenant_id: str) -> list[TenantMembership]: ...
    def grant(
        self, tenant_id: str, actor_id: str, membership_role: MembershipRole, *, granted_by: str
    ) -> TenantMembership: ...
    def update(
        self, tenant_id: str, actor_id: str, membership_role: MembershipRole,
        status: MembershipStatus, *, changed_by: str
    ) -> TenantMembership: ...
    def create_invitation(self, invitation_id: str, tenant_id: str, invited_actor_id: str,
                          token_sha256: str, expires_at: datetime, *, created_by: str) -> TenantInvitation: ...
    def list_invitations(self, tenant_id: str, *, checked_by: str, limit: int = 100) -> list[TenantInvitation]: ...
    def revoke_invitation(self, tenant_id: str, invitation_id: str, *, revoked_by: str) -> TenantInvitation: ...
    def redeem_invitation(self, token_sha256: str, actor_id: str, *, tenant_id: str) -> TenantMembership: ...
    def list_invitation_events(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...
    def list_events(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...


class InMemoryTenantMembershipRepository:
    """Thread-safe process-local registry for tests and single-process development only."""

    def __init__(self) -> None:
        self._memberships: dict[tuple[str, str], TenantMembership] = {}
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._invitations: dict[str, TenantInvitation] = {}
        self._invitation_events: list[dict[str, Any]] = []
        self._next_invitation_event_id = 1
        self._lock = RLock()

    def _append_event(self, membership: TenantMembership, action: str, performed_by: str) -> None:
        self._events.append({
            "event_id": self._next_event_id,
            "tenant_id": membership.tenant_id,
            "subject_actor_id": membership.actor_id,
            "action": action,
            "membership_role": membership.membership_role,
            "status": membership.status,
            "performed_by": performed_by,
            "occurred_at": datetime.now(timezone.utc),
        })
        self._next_event_id += 1

    def get(self, tenant_id: str, actor_id: str) -> TenantMembership:
        with self._lock:
            try:
                return self._memberships[(tenant_id, actor_id)]
            except KeyError as exc:
                raise KeyError("tenant membership not found") from exc

    def get_active(self, tenant_id: str, actor_id: str) -> TenantMembership:
        membership = self.get(tenant_id, actor_id)
        if membership.status != "active":
            raise KeyError("active tenant membership not found")
        return membership

    def list_for_tenant(self, tenant_id: str) -> list[TenantMembership]:
        with self._lock:
            return sorted(
                (item for item in self._memberships.values() if item.tenant_id == tenant_id),
                key=lambda item: item.actor_id,
            )

    def grant(
        self,
        tenant_id: str,
        actor_id: str,
        membership_role: MembershipRole,
        *,
        granted_by: str,
    ) -> TenantMembership:
        now = datetime.now(timezone.utc)
        # Validate the same shape used by the durable repository.
        probe = TenantMembership(
            tenant_id=tenant_id, actor_id=actor_id, membership_role=membership_role,
            status="active", created_at=now, updated_at=now,
            created_by=granted_by, updated_by=granted_by,
        )
        with self._lock:
            key = (probe.tenant_id, probe.actor_id)
            previous = self._memberships.get(key)
            if previous and previous.status == "active" and previous.membership_role == membership_role:
                return previous
            if previous and previous.membership_role == "tenant_admin" and membership_role != "tenant_admin":
                raise ValueError("tenant-admin roles must be changed through platform administration")
            if previous:
                action = "reactivated" if previous.status != "active" else "role_changed"
                membership = TenantMembership(
                    tenant_id=probe.tenant_id, actor_id=probe.actor_id, membership_role=membership_role,
                    status="active", created_at=previous.created_at, updated_at=now,
                    created_by=previous.created_by, updated_by=probe.updated_by,
                )
            else:
                action = "created"
                membership = probe
            self._memberships[key] = membership
            self._append_event(membership, action, probe.updated_by)
            return membership

    def update(
        self,
        tenant_id: str,
        actor_id: str,
        membership_role: MembershipRole,
        status: MembershipStatus,
        *,
        changed_by: str,
    ) -> TenantMembership:
        now = datetime.now(timezone.utc)
        with self._lock:
            key = (tenant_id, actor_id)
            try:
                previous = self._memberships[key]
            except KeyError as exc:
                raise KeyError("tenant membership not found") from exc
            if previous.membership_role == membership_role and previous.status == status:
                return previous
            if previous.membership_role == "tenant_admin" and previous.status == "active" and (
                membership_role != "tenant_admin" or status != "active"
            ):
                self._ensure_another_admin(tenant_id, actor_id)
            if status == "revoked":
                action = "revoked"
            elif status == "suspended":
                action = "suspended"
            elif previous.status != "active":
                action = "reactivated"
            else:
                action = "role_changed"
            updated = TenantMembership(
                tenant_id=tenant_id, actor_id=actor_id, membership_role=membership_role,
                status=status, created_at=previous.created_at, updated_at=now,
                created_by=previous.created_by, updated_by=changed_by,
            )
            self._memberships[key] = updated
            self._append_event(updated, action, changed_by)
            return updated

    def _ensure_another_admin(self, tenant_id: str, excluded_actor_id: str) -> None:
        remaining = sum(
            item.tenant_id == tenant_id
            and item.actor_id != excluded_actor_id
            and item.membership_role == "tenant_admin"
            and item.status == "active"
            for item in self._memberships.values()
        )
        if remaining < 1:
            raise LastTenantAdminError("tenant must retain at least one active administrator")


    def _append_invitation_event(self, invitation: TenantInvitation, action: str, performed_by: str) -> None:
        self._invitation_events.append({
            "event_id": self._next_invitation_event_id, "invitation_id": invitation.invitation_id,
            "tenant_id": invitation.tenant_id, "invited_actor_id": invitation.invited_actor_id,
            "action": action, "status": invitation.status, "performed_by": performed_by,
            "occurred_at": datetime.now(timezone.utc),
        })
        self._next_invitation_event_id += 1

    def create_invitation(self, invitation_id: str, tenant_id: str, invited_actor_id: str,
                          token_sha256: str, expires_at: datetime, *, created_by: str) -> TenantInvitation:
        now = datetime.now(timezone.utc)
        item = TenantInvitation(invitation_id, tenant_id, invited_actor_id, token_sha256,
                                "pending", now, expires_at, created_by)
        if item.expires_at <= now:
            raise ValueError("invitation expiry must be in the future")
        with self._lock:
            existing = self._memberships.get((item.tenant_id, item.invited_actor_id))
            if existing and (existing.status == "active" or existing.membership_role == "tenant_admin"):
                raise InvitationConflictError("actor already has a membership that cannot be invited")
            for old_hash, old in list(self._invitations.items()):
                if old.tenant_id == item.tenant_id and old.invited_actor_id == item.invited_actor_id and old.status == "pending":
                    if old.expires_at <= now:
                        expired_item = replace(old, status="expired")
                        self._invitations[old_hash] = expired_item
                        self._append_invitation_event(expired_item, "expired", item.created_by)
                    else:
                        raise InvitationConflictError("an invitation is already pending for this actor")
            if item.token_sha256 in self._invitations:
                raise InvitationConflictError("invitation token collision")
            self._invitations[item.token_sha256] = item
            self._append_invitation_event(item, "created", item.created_by)
            return item

    def list_invitations(self, tenant_id: str, *, checked_by: str, limit: int = 100) -> list[TenantInvitation]:
        if not 1 <= limit <= 500:
            raise ValueError("invitation limit must be between 1 and 500")
        now = datetime.now(timezone.utc)
        with self._lock:
            for token_hash, item in list(self._invitations.items()):
                if item.tenant_id == tenant_id and item.status == "pending" and item.expires_at <= now:
                    updated = replace(item, status="expired")
                    self._invitations[token_hash] = updated
                    self._append_invitation_event(updated, "expired", checked_by)
            items = [item for item in self._invitations.values() if item.tenant_id == tenant_id]
            items.sort(key=lambda i: (i.created_at, i.invitation_id), reverse=True)
            return items[:limit]

    def revoke_invitation(self, tenant_id: str, invitation_id: str, *, revoked_by: str) -> TenantInvitation:
        now = datetime.now(timezone.utc)
        with self._lock:
            pair = next(((h, i) for h, i in self._invitations.items()
                         if i.tenant_id == tenant_id and i.invitation_id == invitation_id), None)
            if pair is None:
                raise KeyError("invitation not found")
            token_hash, item = pair
            if item.status != "pending":
                raise InvitationConflictError("invitation is no longer pending")
            if item.expires_at <= now:
                updated = replace(item, status="expired")
                self._invitations[token_hash] = updated
                self._append_invitation_event(updated, "expired", revoked_by)
                return updated
            updated = replace(item, status="revoked", revoked_at=now, revoked_by=revoked_by)
            self._invitations[token_hash] = updated
            self._append_invitation_event(updated, "revoked", revoked_by)
            return updated

    def redeem_invitation(self, token_sha256: str, actor_id: str, *, tenant_id: str) -> TenantMembership:
        now = datetime.now(timezone.utc)
        expired = False
        membership = None
        with self._lock:
            item = self._invitations.get(token_sha256)
            if (item is None or item.invited_actor_id != actor_id or item.tenant_id != tenant_id
                    or item.status != "pending"):
                raise InvitationUnavailableError("invitation is invalid or unavailable")
            if item.expires_at <= now:
                updated = replace(item, status="expired")
                self._invitations[token_sha256] = updated
                self._append_invitation_event(updated, "expired", actor_id)
                expired = True
            else:
                key = (item.tenant_id, actor_id)
                prior = self._memberships.get(key)
                if prior and prior.membership_role == "tenant_admin":
                    raise InvitationConflictError("tenant administrator membership requires platform administration")
                if prior and prior.status == "active":
                    raise InvitationConflictError("actor already has an active tenant membership")
                if prior:
                    membership = TenantMembership(item.tenant_id, actor_id, "member", "active",
                        prior.created_at, now, prior.created_by, actor_id)
                    action = "reactivated"
                else:
                    membership = TenantMembership(item.tenant_id, actor_id, "member", "active",
                        now, now, item.created_by, actor_id)
                    action = "created"
                self._memberships[key] = membership
                self._append_event(membership, action, actor_id)
                redeemed = replace(item, status="redeemed", redeemed_at=now, redeemed_by=actor_id)
                self._invitations[token_sha256] = redeemed
                self._append_invitation_event(redeemed, "redeemed", actor_id)
        if expired or membership is None:
            raise InvitationUnavailableError("invitation is invalid or unavailable")
        return membership

    def list_invitation_events(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("invitation event limit must be between 1 and 500")
        with self._lock:
            events = [e for e in self._invitation_events if e["tenant_id"] == tenant_id]
            return [dict(e) for e in reversed(events[-limit:])]

    def list_events(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("audit event limit must be between 1 and 500")
        with self._lock:
            events = [event for event in self._events if event["tenant_id"] == tenant_id]
            return [dict(event) for event in reversed(events[-limit:])]


class PostgresTenantMembershipRepository:
    """Durable tenant membership store; writes and their audit events share one transaction."""

    def __init__(self, connection_factory: Callable[[], Any]):
        self._connection_factory = connection_factory

    @staticmethod
    def _membership(row) -> TenantMembership:
        return TenantMembership(
            tenant_id=row[0], actor_id=row[1], membership_role=row[2], status=row[3],
            created_at=row[4], updated_at=row[5], created_by=row[6], updated_by=row[7],
        )

    @staticmethod
    def _lock_membership_table(cur) -> None:
        # Serialize administrator-removal checks against concurrent membership changes.
        cur.execute("LOCK TABLE ofi_tenant_memberships IN SHARE ROW EXCLUSIVE MODE")

    @staticmethod
    def _ensure_another_admin(cur, tenant_id: str, excluded_actor_id: str) -> None:
        cur.execute(
            """SELECT count(*) FROM ofi_tenant_memberships
               WHERE tenant_id=%s AND actor_id<>%s
                 AND membership_role='tenant_admin' AND status='active'""",
            (tenant_id, excluded_actor_id),
        )
        if cur.fetchone()[0] < 1:
            raise LastTenantAdminError("tenant must retain at least one active administrator")

    @staticmethod
    def _record_event(cur, membership: TenantMembership, action: str, performed_by: str) -> None:
        cur.execute(
            """INSERT INTO ofi_tenant_membership_events
                 (tenant_id, subject_actor_id, action, membership_role, status, performed_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (membership.tenant_id, membership.actor_id, action,
             membership.membership_role, membership.status, performed_by),
        )

    def get(self, tenant_id: str, actor_id: str) -> TenantMembership:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT tenant_id, actor_id, membership_role, status, created_at,
                              updated_at, created_by, updated_by
                       FROM ofi_tenant_memberships WHERE tenant_id=%s AND actor_id=%s""",
                    (tenant_id, actor_id),
                )
                row = cur.fetchone()
        if row is None:
            raise KeyError("tenant membership not found")
        return self._membership(row)

    def get_active(self, tenant_id: str, actor_id: str) -> TenantMembership:
        membership = self.get(tenant_id, actor_id)
        if membership.status != "active":
            raise KeyError("active tenant membership not found")
        return membership

    def list_for_tenant(self, tenant_id: str) -> list[TenantMembership]:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT tenant_id, actor_id, membership_role, status, created_at,
                              updated_at, created_by, updated_by
                       FROM ofi_tenant_memberships WHERE tenant_id=%s ORDER BY actor_id""",
                    (tenant_id,),
                )
                rows = cur.fetchall()
        return [self._membership(row) for row in rows]

    def grant(
        self,
        tenant_id: str,
        actor_id: str,
        membership_role: MembershipRole,
        *,
        granted_by: str,
    ) -> TenantMembership:
        now = datetime.now(timezone.utc)
        probe = TenantMembership(
            tenant_id=tenant_id, actor_id=actor_id, membership_role=membership_role,
            status="active", created_at=now, updated_at=now,
            created_by=granted_by, updated_by=granted_by,
        )
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    self._lock_membership_table(cur)
                    cur.execute(
                        """SELECT tenant_id, actor_id, membership_role, status, created_at,
                                  updated_at, created_by, updated_by
                           FROM ofi_tenant_memberships
                           WHERE tenant_id=%s AND actor_id=%s FOR UPDATE""",
                        (probe.tenant_id, probe.actor_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        cur.execute(
                            """INSERT INTO ofi_tenant_memberships
                                 (tenant_id, actor_id, membership_role, status, created_by, updated_by)
                               VALUES (%s, %s, %s, 'active', %s, %s)
                               RETURNING tenant_id, actor_id, membership_role, status, created_at,
                                         updated_at, created_by, updated_by""",
                            (probe.tenant_id, probe.actor_id, membership_role, granted_by, granted_by),
                        )
                        membership = self._membership(cur.fetchone())
                        action = "created"
                    else:
                        previous = self._membership(row)
                        if previous.status == "active" and previous.membership_role == membership_role:
                            return previous
                        if previous.membership_role == "tenant_admin" and membership_role != "tenant_admin":
                            raise ValueError("tenant-admin roles must be changed through platform administration")
                        cur.execute(
                            """UPDATE ofi_tenant_memberships
                               SET membership_role=%s, status='active', updated_at=%s, updated_by=%s
                               WHERE tenant_id=%s AND actor_id=%s
                               RETURNING tenant_id, actor_id, membership_role, status, created_at,
                                         updated_at, created_by, updated_by""",
                            (membership_role, now, granted_by, probe.tenant_id, probe.actor_id),
                        )
                        membership = self._membership(cur.fetchone())
                        action = "reactivated" if previous.status != "active" else "role_changed"
                    self._record_event(cur, membership, action, granted_by)
                    return membership

    def update(
        self,
        tenant_id: str,
        actor_id: str,
        membership_role: MembershipRole,
        status: MembershipStatus,
        *,
        changed_by: str,
    ) -> TenantMembership:
        now = datetime.now(timezone.utc)
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    self._lock_membership_table(cur)
                    cur.execute(
                        """SELECT tenant_id, actor_id, membership_role, status, created_at,
                                  updated_at, created_by, updated_by
                           FROM ofi_tenant_memberships
                           WHERE tenant_id=%s AND actor_id=%s FOR UPDATE""",
                        (tenant_id, actor_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise KeyError("tenant membership not found")
                    previous = self._membership(row)
                    if previous.membership_role == membership_role and previous.status == status:
                        return previous
                    if previous.membership_role == "tenant_admin" and previous.status == "active" and (
                        membership_role != "tenant_admin" or status != "active"
                    ):
                        self._ensure_another_admin(cur, tenant_id, actor_id)
                    if status == "revoked":
                        action = "revoked"
                    elif status == "suspended":
                        action = "suspended"
                    elif previous.status != "active":
                        action = "reactivated"
                    else:
                        action = "role_changed"
                    cur.execute(
                        """UPDATE ofi_tenant_memberships
                           SET membership_role=%s, status=%s, updated_at=%s, updated_by=%s
                           WHERE tenant_id=%s AND actor_id=%s
                           RETURNING tenant_id, actor_id, membership_role, status, created_at,
                                     updated_at, created_by, updated_by""",
                        (membership_role, status, now, changed_by, tenant_id, actor_id),
                    )
                    membership = self._membership(cur.fetchone())
                    self._record_event(cur, membership, action, changed_by)
                    return membership


    @staticmethod
    def _invitation(row) -> TenantInvitation:
        return TenantInvitation(
            invitation_id=row[0], tenant_id=row[1], invited_actor_id=row[2], token_sha256=row[3],
            status=row[4], created_at=row[5], expires_at=row[6], created_by=row[7],
            redeemed_at=row[8], redeemed_by=row[9], revoked_at=row[10], revoked_by=row[11],
        )

    @staticmethod
    def _record_invitation_event(cur, invitation: TenantInvitation, action: str, performed_by: str) -> None:
        cur.execute(
            """INSERT INTO ofi_tenant_invitation_events
               (invitation_id, tenant_id, invited_actor_id, action, status, performed_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (invitation.invitation_id, invitation.tenant_id, invitation.invited_actor_id,
             action, invitation.status, performed_by),
        )

    def create_invitation(self, invitation_id: str, tenant_id: str, invited_actor_id: str,
                          token_sha256: str, expires_at: datetime, *, created_by: str) -> TenantInvitation:
        now = datetime.now(timezone.utc)
        item = TenantInvitation(invitation_id, tenant_id, invited_actor_id, token_sha256,
                                "pending", now, expires_at, created_by)
        if item.expires_at <= now:
            raise ValueError("invitation expiry must be in the future")
        select = """SELECT invitation_id, tenant_id, invited_actor_id, token_sha256, status,
                           created_at, expires_at, created_by, redeemed_at, redeemed_by, revoked_at, revoked_by
                    FROM ofi_tenant_invitations"""
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("LOCK TABLE ofi_tenant_invitations IN SHARE ROW EXCLUSIVE MODE")
                    cur.execute(select + " WHERE tenant_id=%s AND invited_actor_id=%s AND status='pending' FOR UPDATE",
                                (item.tenant_id, item.invited_actor_id))
                    for row in cur.fetchall():
                        old = self._invitation(row)
                        if old.expires_at <= now:
                            cur.execute("UPDATE ofi_tenant_invitations SET status='expired' WHERE invitation_id=%s",
                                        (old.invitation_id,))
                            self._record_invitation_event(cur, replace(old, status="expired"), "expired", item.created_by)
                        else:
                            raise InvitationConflictError("an invitation is already pending for this actor")
                    self._lock_membership_table(cur)
                    cur.execute("SELECT membership_role, status FROM ofi_tenant_memberships WHERE tenant_id=%s AND actor_id=%s FOR UPDATE",
                                (item.tenant_id, item.invited_actor_id))
                    membership = cur.fetchone()
                    if membership and (membership[1] == "active" or membership[0] == "tenant_admin"):
                        raise InvitationConflictError("actor already has a membership that cannot be invited")
                    cur.execute(
                        """INSERT INTO ofi_tenant_invitations
                           (invitation_id, tenant_id, invited_actor_id, token_sha256, status, created_at, expires_at, created_by)
                           VALUES (%s,%s,%s,%s,'pending',%s,%s,%s)
                           RETURNING invitation_id, tenant_id, invited_actor_id, token_sha256, status,
                                     created_at, expires_at, created_by, redeemed_at, redeemed_by, revoked_at, revoked_by""",
                        (item.invitation_id,item.tenant_id,item.invited_actor_id,item.token_sha256,item.created_at,item.expires_at,item.created_by))
                    created = self._invitation(cur.fetchone())
                    self._record_invitation_event(cur, created, "created", created.created_by)
                    return created

    def list_invitations(self, tenant_id: str, *, checked_by: str, limit: int = 100) -> list[TenantInvitation]:
        if not 1 <= limit <= 500:
            raise ValueError("invitation limit must be between 1 and 500")
        now = datetime.now(timezone.utc)
        select = """SELECT invitation_id, tenant_id, invited_actor_id, token_sha256, status,
                           created_at, expires_at, created_by, redeemed_at, redeemed_by, revoked_at, revoked_by
                    FROM ofi_tenant_invitations"""
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("LOCK TABLE ofi_tenant_invitations IN SHARE ROW EXCLUSIVE MODE")
                    cur.execute(select + " WHERE tenant_id=%s AND status='pending' AND expires_at<=%s FOR UPDATE",
                                (tenant_id, now))
                    for row in cur.fetchall():
                        old = self._invitation(row)
                        updated = replace(old, status="expired")
                        cur.execute("UPDATE ofi_tenant_invitations SET status='expired' WHERE invitation_id=%s",
                                    (old.invitation_id,))
                        self._record_invitation_event(cur, updated, "expired", checked_by)
                    cur.execute(select + " WHERE tenant_id=%s ORDER BY created_at DESC, invitation_id DESC LIMIT %s",
                                (tenant_id, limit))
                    rows = cur.fetchall()
        return [self._invitation(row) for row in rows]

    def revoke_invitation(self, tenant_id: str, invitation_id: str, *, revoked_by: str) -> TenantInvitation:
        now = datetime.now(timezone.utc)
        select = """SELECT invitation_id, tenant_id, invited_actor_id, token_sha256, status,
                           created_at, expires_at, created_by, redeemed_at, redeemed_by, revoked_at, revoked_by
                    FROM ofi_tenant_invitations"""
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(select + " WHERE tenant_id=%s AND invitation_id=%s FOR UPDATE", (tenant_id, invitation_id))
                    row = cur.fetchone()
                    if row is None:
                        raise KeyError("invitation not found")
                    old = self._invitation(row)
                    if old.status != "pending":
                        raise InvitationConflictError("invitation is no longer pending")
                    if old.expires_at <= now:
                        cur.execute(
                            """UPDATE ofi_tenant_invitations SET status='expired' WHERE invitation_id=%s
                               RETURNING invitation_id, tenant_id, invited_actor_id, token_sha256, status, created_at,
                                         expires_at, created_by, redeemed_at, redeemed_by, revoked_at, revoked_by""",
                            (invitation_id,))
                        updated = self._invitation(cur.fetchone())
                        self._record_invitation_event(cur, updated, "expired", revoked_by)
                    else:
                        cur.execute(
                            """UPDATE ofi_tenant_invitations SET status='revoked', revoked_at=%s, revoked_by=%s
                               WHERE invitation_id=%s
                               RETURNING invitation_id, tenant_id, invited_actor_id, token_sha256, status, created_at,
                                         expires_at, created_by, redeemed_at, redeemed_by, revoked_at, revoked_by""",
                            (now, revoked_by, invitation_id))
                        updated = self._invitation(cur.fetchone())
                        self._record_invitation_event(cur, updated, "revoked", revoked_by)
                    return updated

    def redeem_invitation(self, token_sha256: str, actor_id: str, *, tenant_id: str) -> TenantMembership:
        now = datetime.now(timezone.utc)
        expired = False
        membership: TenantMembership | None = None
        select = """SELECT invitation_id, tenant_id, invited_actor_id, token_sha256, status,
                           created_at, expires_at, created_by, redeemed_at, redeemed_by, revoked_at, revoked_by
                    FROM ofi_tenant_invitations"""
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(select + " WHERE token_sha256=%s FOR UPDATE", (token_sha256,))
                    row = cur.fetchone()
                    if row is None:
                        raise InvitationUnavailableError("invitation is invalid or unavailable")
                    invite = self._invitation(row)
                    if invite.invited_actor_id != actor_id or invite.tenant_id != tenant_id or invite.status != "pending":
                        raise InvitationUnavailableError("invitation is invalid or unavailable")
                    if invite.expires_at <= now:
                        cur.execute(
                            """UPDATE ofi_tenant_invitations SET status='expired' WHERE invitation_id=%s
                               RETURNING invitation_id, tenant_id, invited_actor_id, token_sha256, status, created_at,
                                         expires_at, created_by, redeemed_at, redeemed_by, revoked_at, revoked_by""",
                            (invite.invitation_id,))
                        updated = self._invitation(cur.fetchone())
                        self._record_invitation_event(cur, updated, "expired", actor_id)
                        expired = True
                    else:
                        self._lock_membership_table(cur)
                        cur.execute(
                            """SELECT tenant_id, actor_id, membership_role, status, created_at, updated_at,
                                      created_by, updated_by FROM ofi_tenant_memberships
                               WHERE tenant_id=%s AND actor_id=%s FOR UPDATE""",
                            (invite.tenant_id, actor_id))
                        old_row = cur.fetchone()
                        if old_row is not None:
                            old = self._membership(old_row)
                            if old.membership_role == "tenant_admin":
                                raise InvitationConflictError("tenant administrator membership requires platform administration")
                            if old.status == "active":
                                raise InvitationConflictError("actor already has an active tenant membership")
                            cur.execute(
                                """UPDATE ofi_tenant_memberships SET membership_role='member',status='active',
                                   updated_at=%s,updated_by=%s WHERE tenant_id=%s AND actor_id=%s
                                   RETURNING tenant_id, actor_id, membership_role, status, created_at, updated_at, created_by, updated_by""",
                                (now, actor_id, invite.tenant_id, actor_id))
                            membership = self._membership(cur.fetchone())
                            action = "reactivated"
                        else:
                            cur.execute(
                                """INSERT INTO ofi_tenant_memberships
                                   (tenant_id, actor_id, membership_role, status, created_at, updated_at, created_by, updated_by)
                                   VALUES (%s,%s,'member','active',%s,%s,%s,%s)
                                   RETURNING tenant_id, actor_id, membership_role, status, created_at, updated_at, created_by, updated_by""",
                                (invite.tenant_id, actor_id, now, now, invite.created_by, actor_id))
                            membership = self._membership(cur.fetchone())
                            action = "created"
                        self._record_event(cur, membership, action, actor_id)
                        cur.execute(
                            """UPDATE ofi_tenant_invitations SET status='redeemed',redeemed_at=%s,redeemed_by=%s
                               WHERE invitation_id=%s
                               RETURNING invitation_id, tenant_id, invited_actor_id, token_sha256, status, created_at,
                                         expires_at, created_by, redeemed_at, redeemed_by, revoked_at, revoked_by""",
                            (now, actor_id, invite.invitation_id))
                        redeemed = self._invitation(cur.fetchone())
                        self._record_invitation_event(cur, redeemed, "redeemed", actor_id)
        if expired or membership is None:
            raise InvitationUnavailableError("invitation is invalid or unavailable")
        return membership

    def list_invitation_events(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("invitation event limit must be between 1 and 500")
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT event_id, invitation_id, tenant_id, invited_actor_id, action, status, performed_by, occurred_at
                       FROM ofi_tenant_invitation_events WHERE tenant_id=%s ORDER BY event_id DESC LIMIT %s""",
                    (tenant_id, limit))
                rows = cur.fetchall()
        return [{"event_id":r[0],"invitation_id":r[1],"tenant_id":r[2],"invited_actor_id":r[3],
                 "action":r[4],"status":r[5],"performed_by":r[6],"occurred_at":r[7]} for r in rows]

    def list_events(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("audit event limit must be between 1 and 500")
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT event_id, tenant_id, subject_actor_id, action,
                              membership_role, status, performed_by, occurred_at
                       FROM ofi_tenant_membership_events
                       WHERE tenant_id=%s ORDER BY event_id DESC LIMIT %s""",
                    (tenant_id, limit),
                )
                rows = cur.fetchall()
        return [
            {
                "event_id": row[0], "tenant_id": row[1], "subject_actor_id": row[2],
                "action": row[3], "membership_role": row[4], "status": row[5],
                "performed_by": row[6], "occurred_at": row[7],
            }
            for row in rows
        ]
