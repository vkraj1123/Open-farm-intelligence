"""PostgreSQL integration tests for the durable consent ledger."""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from ofi.services.consent_repository import (
    ConsentConflictError,
    PostgresConsentRepository,
)
from ofi.services.execution_gateway import ConsentGrant
from ofi.twin.migrate import apply_schema


DATABASE_URL = os.environ.get("OFI_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires PostgreSQL CI service via OFI_POSTGRES_DSN",
)


def make_grant(*, consent_id=None, scopes=("soil_test",)):
    now = datetime.now(timezone.utc)
    return ConsentGrant(
        actor_id="postgres-consent-test-actor",
        purpose="farm diagnostics",
        status="granted",
        granted_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=2),
        scopes=scopes,
        consent_id=consent_id or f"consent:{uuid4().hex}",
    )


def test_postgres_consent_terms_are_immutable_and_revocation_is_owner_bound():
    assert apply_schema(DATABASE_URL) >= 3
    store = PostgresConsentRepository(lambda: psycopg.connect(DATABASE_URL))
    grant = make_grant()

    created = store.create(grant)
    assert created == grant
    assert store.create(grant) == grant
    assert store.get(grant.consent_id) == grant

    replacement = make_grant(consent_id=grant.consent_id, scopes=("KVK",))
    with pytest.raises(ConsentConflictError):
        store.create(replacement)

    with pytest.raises(KeyError, match="not found"):
        store.revoke(grant.consent_id, actor_id="different-actor")

    revoked_at = datetime.now(timezone.utc)
    revoked = store.revoke(
        grant.consent_id,
        actor_id=grant.actor_id,
        revoked_at=revoked_at,
    )
    repeated = store.revoke(
        grant.consent_id,
        actor_id=grant.actor_id,
        revoked_at=revoked_at + timedelta(minutes=5),
    )
    assert revoked.revoked_at == revoked_at
    assert repeated.revoked_at == revoked_at
    assert not store.get(grant.consent_id).active()

    with pytest.raises(psycopg.errors.RaiseException, match="terms are immutable"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "UPDATE ofi_consent_grants SET purpose='rewritten' WHERE consent_id=%s",
                (grant.consent_id,),
            )
    with pytest.raises(psycopg.errors.RaiseException, match="cannot be deleted"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "DELETE FROM ofi_consent_grants WHERE consent_id=%s",
                (grant.consent_id,),
            )


def test_postgres_consent_revocation_rejects_naive_or_pre_grant_timestamps():
    assert apply_schema(DATABASE_URL) >= 3
    store = PostgresConsentRepository(lambda: psycopg.connect(DATABASE_URL))
    grant = make_grant()
    store.create(grant)

    with pytest.raises(ValueError, match="timezone-aware"):
        store.revoke(
            grant.consent_id,
            actor_id=grant.actor_id,
            revoked_at=datetime(2026, 10, 1, 12, 0),
        )
    with pytest.raises(ValueError, match="revocation cannot precede"):
        store.revoke(
            grant.consent_id,
            actor_id=grant.actor_id,
            revoked_at=grant.granted_at - timedelta(seconds=1),
        )
    assert store.get(grant.consent_id).revoked_at is None
