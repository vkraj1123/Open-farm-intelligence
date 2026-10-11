"""Consent repository contracts and safe in-memory/PostgreSQL implementations.

Consent grants are immutable records after issuance. The only permitted update
is the first revocation timestamp; PostgreSQL enforces that rule with triggers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Protocol

from ofi.services.execution_gateway import ConsentGrant


class ConsentConflictError(RuntimeError):
    """A consent ID is already bound to a different grant."""


class ConsentRepository(Protocol):
    """Authoritative lookup and revocation store used by execution gateways."""

    def create(self, grant: ConsentGrant) -> ConsentGrant:
        """Persist one immutable, not-yet-revoked grant."""
        ...

    def get(self, consent_id: str, tenant_id: str | None = None) -> ConsentGrant:
        """Return the authoritative grant or raise KeyError."""
        ...

    def revoke(
        self,
        consent_id: str,
        *,
        actor_id: str,
        tenant_id: str | None = None,
        revoked_at: datetime | None = None,
    ) -> ConsentGrant:
        """Revoke for the owning actor; first revocation timestamp wins."""
        ...


class InMemoryConsentRepository:
    """Thread-safe reference store for tests and single-process development.

    Revocations in this implementation are process-local. Multi-process or
    production deployment must use PostgresConsentRepository or another shared
    durable implementation.
    """

    def __init__(self) -> None:
        self._grants: dict[str, ConsentGrant] = {}
        self._lock = RLock()

    def create(self, grant: ConsentGrant) -> ConsentGrant:
        if grant.revoked_at is not None:
            raise ValueError("a new consent grant cannot already be revoked")
        with self._lock:
            previous = self._grants.get(grant.consent_id)
            if previous is not None:
                if previous != grant:
                    raise ConsentConflictError(
                        "consent ID is already bound to a different grant"
                    )
                return previous
            self._grants[grant.consent_id] = grant
            return grant

    def get(self, consent_id: str, tenant_id: str | None = None) -> ConsentGrant:
        with self._lock:
            try:
                grant = self._grants[consent_id]
            except KeyError as exc:
                raise KeyError("consent grant not found") from exc
            if tenant_id is not None and grant.tenant_id != tenant_id:
                raise KeyError("consent grant not found")
            return grant

    def revoke(
        self,
        consent_id: str,
        *,
        actor_id: str,
        tenant_id: str | None = None,
        revoked_at: datetime | None = None,
    ) -> ConsentGrant:
        moment = revoked_at or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("revoked_at must be timezone-aware")
        with self._lock:
            try:
                grant = self.get(consent_id, tenant_id=tenant_id)
            except KeyError as exc:
                raise KeyError("consent grant not found") from exc
            if grant.actor_id != actor_id:
                # Do not distinguish unknown IDs from grants owned by another actor.
                raise KeyError("consent grant not found")
            if grant.revoked_at is not None:
                return grant
            revoked = grant.with_revocation(moment)
            self._grants[consent_id] = revoked
            return revoked


class PostgresConsentRepository:
    """PostgreSQL-backed consent ledger with immutable grant fields.

    The database must have migration 003 applied. Supply the same style of
    connection factory used by OFI's other PostgreSQL repositories.
    """

    def __init__(self, connection_factory: Callable[[], object]):
        self._connection_factory = connection_factory

    @staticmethod
    def _grant(row) -> ConsentGrant:
        (
            consent_id,
            actor_id,
            purpose,
            status,
            scopes,
            granted_at,
            expires_at,
            revoked_at,
            tenant_id,
        ) = row
        return ConsentGrant(
            actor_id=actor_id,
            purpose=purpose,
            status=status,
            granted_at=granted_at,
            expires_at=expires_at,
            scopes=tuple(scopes or ()),
            consent_id=consent_id,
            revoked_at=revoked_at,
            tenant_id=tenant_id,
        )

    def create(self, grant: ConsentGrant) -> ConsentGrant:
        if grant.revoked_at is not None:
            raise ValueError("a new consent grant cannot already be revoked")
        if grant.status == "not_required":
            raise ValueError("not_required is not a storable consent decision")
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                INSERT INTO ofi_consent_grants
                    (consent_id, tenant_id, actor_id, purpose, status, scopes,
                     granted_at, expires_at, revoked_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (consent_id) DO NOTHING
                RETURNING consent_id, actor_id, purpose, status, scopes,
                          granted_at, expires_at, revoked_at, tenant_id
                """,
                (
                    grant.consent_id,
                    grant.tenant_id,
                    grant.actor_id,
                    grant.purpose,
                    grant.status,
                    list(grant.scopes),
                    grant.granted_at,
                    grant.expires_at,
                ),
            ).fetchone()
            if row is not None:
                return self._grant(row)

        existing = self.get(grant.consent_id)
        if existing != grant:
            raise ConsentConflictError(
                "consent ID is already bound to a different grant"
            )
        return existing

    def get(self, consent_id: str, tenant_id: str | None = None) -> ConsentGrant:
        with self._connection_factory() as connection:
            if tenant_id is None:
                row = connection.execute(
                    """
                    SELECT consent_id, actor_id, purpose, status, scopes,
                           granted_at, expires_at, revoked_at, tenant_id
                    FROM ofi_consent_grants
                    WHERE consent_id = %s
                    """,
                    (consent_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT consent_id, actor_id, purpose, status, scopes,
                           granted_at, expires_at, revoked_at, tenant_id
                    FROM ofi_consent_grants
                    WHERE consent_id = %s AND tenant_id = %s
                    """,
                    (consent_id, tenant_id),
                ).fetchone()
        if row is None:
            raise KeyError("consent grant not found")
        return self._grant(row)

    def revoke(
        self,
        consent_id: str,
        *,
        actor_id: str,
        tenant_id: str | None = None,
        revoked_at: datetime | None = None,
    ) -> ConsentGrant:
        moment = revoked_at or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("revoked_at must be timezone-aware")
        current = self.get(consent_id, tenant_id=tenant_id)
        if current.actor_id != actor_id:
            raise KeyError("consent grant not found")
        # Validate the temporal rule before relying on PostgreSQL constraints.
        current.with_revocation(moment)
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                UPDATE ofi_consent_grants
                SET revoked_at = COALESCE(revoked_at, %s)
                WHERE consent_id = %s AND actor_id = %s
                  AND (%s::text IS NULL OR tenant_id = %s)
                RETURNING consent_id, actor_id, purpose, status, scopes,
                          granted_at, expires_at, revoked_at, tenant_id
                """,
                (moment, consent_id, actor_id, tenant_id, tenant_id),
            ).fetchone()
        if row is None:
            # Avoid exposing another actor's grant-existence information.
            raise KeyError("consent grant not found")
        return self._grant(row)
