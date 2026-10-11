"""Authoritative tenant membership registry and append-only admin audit trail."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Literal, Protocol

from ofi.services.db_context import connection_scope

MembershipRole = Literal["tenant_admin", "member"]
MembershipStatus = Literal["active", "suspended", "revoked"]


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
    def list_events(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...


class InMemoryTenantMembershipRepository:
    """Thread-safe process-local registry for tests and single-process development only."""

    def __init__(self) -> None:
        self._memberships: dict[tuple[str, str], TenantMembership] = {}
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
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
