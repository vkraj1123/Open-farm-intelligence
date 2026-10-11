"""Consent scope, temporal, actor-binding and revocation regression tests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from ofi.services.action_router import ActionRequest, ActionRouter
from ofi.services.consent_repository import (
    ConsentConflictError,
    InMemoryConsentRepository,
)
from ofi.services.execution_gateway import (
    ActorIdentity,
    ConsentGrant,
    ExecutionError,
    ExecutionRequest,
    MockServiceAdapter,
    ServiceExecutionGateway,
)
from ofi.services.service_transaction import ExecutionAttempt, ServiceTransaction
from ofi.services.transaction_repository import InMemoryTransactionRepository


def action(service="soil_test"):
    return ActionRequest(
        id=f"case-1-action-{service}",
        case_id="case-1",
        farm_id="farm-1",
        action="REQUEST_TEST",
        service=service,
        confidence=0.8,
        rationale="Verify a farm condition.",
    )


def grant(*, actor_id="farmer-1", scopes=("soil_test",), purpose="field diagnosis", **kwargs):
    now = datetime.now(timezone.utc)
    return ConsentGrant(
        actor_id=actor_id,
        purpose=purpose,
        status="granted",
        granted_at=kwargs.pop("granted_at", now - timedelta(minutes=1)),
        expires_at=kwargs.pop("expires_at", now + timedelta(hours=1)),
        scopes=scopes,
        consent_id=kwargs.pop("consent_id", f"consent:{uuid4().hex}"),
        **kwargs,
    )


def test_scopes_are_explicit_and_single_scope_legacy_grants_remain_narrow():
    scoped = grant(scopes=("soil_test", "crop_disease_diagnosis"))

    assert scoped.authorizes("soil_test")
    assert scoped.authorizes("crop_disease_diagnosis")
    assert not scoped.authorizes("KVK")
    assert not scoped.authorizes("*")

    legacy = ConsentGrant(
        "farmer-1",
        "soil_test",
        "granted",
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    assert legacy.authorizes("soil_test")
    assert not legacy.authorizes("crop_disease_diagnosis")


def test_consent_active_checks_grant_time_expiry_and_revocation():
    now = datetime.now(timezone.utc)
    future = grant(
        granted_at=now + timedelta(minutes=5),
        expires_at=now + timedelta(hours=2),
    )
    expired = grant(
        granted_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    )
    revoked = grant(revoked_at=now)

    assert not future.active(as_of=now)
    assert not expired.active(as_of=now)
    assert not revoked.active(as_of=now)
    assert revoked.revoked_at is not None
    assert revoked.active(as_of=revoked.revoked_at - timedelta(seconds=1))


def test_consent_rejects_naive_or_inconsistent_timestamps():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="timezone-aware"):
        grant(granted_at=datetime(2026, 10, 1, 12, 0))
    with pytest.raises(ValueError, match="later than grant time"):
        grant(granted_at=now, expires_at=now)
    with pytest.raises(ValueError, match="revocation cannot precede"):
        grant(granted_at=now, expires_at=now + timedelta(hours=1),
              revoked_at=now - timedelta(seconds=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        grant().active(as_of=datetime(2026, 10, 1, 12, 0))


def test_consent_rejects_blank_or_duplicate_scopes():
    with pytest.raises(ValueError, match="non-empty strings"):
        grant(scopes=("soil_test", " "))
    with pytest.raises(ValueError, match="unique"):
        grant(scopes=("soil_test", "soil_test"))


def test_gateway_rejects_consent_for_an_ungranted_service():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    request = action("soil_test")
    wrong_scope = grant(scopes=("crop_disease_diagnosis",))

    with pytest.raises(ExecutionError, match="does not authorize"):
        gateway.submit(
            action=request,
            route=ActionRouter().route_action(request),
            actor=ActorIdentity("farmer-1", "farmer"),
            consent=wrong_scope,
        )


def test_gateway_rejects_route_that_does_not_match_action():
    gateway = ServiceExecutionGateway([
        MockServiceAdapter("soil_test"),
        MockServiceAdapter("crop_disease_diagnosis"),
    ])
    request = action("soil_test")
    other_route = ActionRouter().route_action(action("crop_disease_diagnosis"))

    with pytest.raises(ExecutionError, match="route does not match"):
        gateway.submit(
            action=request,
            route=other_route,
            actor=ActorIdentity("farmer-1", "farmer"),
            consent=grant(scopes=("soil_test", "crop_disease_diagnosis")),
        )


def test_revoked_and_expired_grants_cannot_submit_actions():
    now = datetime.now(timezone.utc)
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    request = action()
    route = ActionRouter().route_action(request)
    actor = ActorIdentity("farmer-1", "farmer")
    for consent in (
        grant(revoked_at=now),
        grant(
            granted_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        ),
    ):
        with pytest.raises(ExecutionError, match="consent is not active"):
            gateway.submit(action=request, route=route, actor=actor, consent=consent)


def test_in_memory_consent_revocation_is_owner_bound_idempotent_and_immutable():
    store = InMemoryConsentRepository()
    original = grant()
    store.create(original)
    moment = datetime.now(timezone.utc)

    with pytest.raises(KeyError, match="not found"):
        store.revoke(original.consent_id, actor_id="another-farmer", revoked_at=moment)

    revoked = store.revoke(original.consent_id, actor_id=original.actor_id, revoked_at=moment)
    repeated = store.revoke(
        original.consent_id,
        actor_id=original.actor_id,
        revoked_at=moment + timedelta(minutes=5),
    )

    assert revoked.revoked_at == moment
    assert repeated.revoked_at == moment
    assert store.get(original.consent_id) == revoked
    assert original.revoked_at is None
    assert original.active()
    assert not revoked.active()


def test_consent_repository_cannot_rebind_an_existing_consent_id():
    store = InMemoryConsentRepository()
    original = grant(consent_id="consent:fixed")
    store.create(original)
    store.create(original)  # Exact replays are idempotent.

    replacement = grant(consent_id="consent:fixed", scopes=("KVK",))
    with pytest.raises(ConsentConflictError, match="different grant"):
        store.create(replacement)


def test_gateway_uses_authoritative_consent_and_checks_revocation_on_replay():
    class CountingAdapter(MockServiceAdapter):
        calls = 0

        def execute(self, request):
            self.calls += 1
            return super().execute(request)

    store = InMemoryConsentRepository()
    consent = grant()
    store.create(consent)
    adapter = CountingAdapter("soil_test")
    gateway = ServiceExecutionGateway([adapter], consent_repository=store)
    request = action()
    route = ActionRouter().route_action(request)
    actor = ActorIdentity("farmer-1", "farmer")

    first = gateway.submit(
        action=request,
        route=route,
        actor=actor,
        consent=consent,
        idempotency_key="consent-replay-key",
    )
    assert first.status == "submitted"
    assert adapter.calls == 1

    store.revoke(consent.consent_id, actor_id=actor.actor_id)
    # Idempotent replay must still pass the live consent check. A previously
    # accepted consent snapshot cannot bypass the authoritative revocation.
    with pytest.raises(ExecutionError, match="consent is not active"):
        gateway.submit(
            action=request,
            route=route,
            actor=actor,
            consent=consent,
            idempotency_key="consent-replay-key",
        )
    assert adapter.calls == 1


def test_gateway_rejects_consent_snapshot_for_another_actor():
    store = InMemoryConsentRepository()
    stored = grant(actor_id="owner-1")
    store.create(stored)
    gateway = ServiceExecutionGateway(
        [MockServiceAdapter("soil_test")],
        consent_repository=store,
    )
    request = action()

    with pytest.raises(ExecutionError, match="does not match authenticated actor"):
        gateway.submit(
            action=request,
            route=ActionRouter().route_action(request),
            actor=ActorIdentity("farmer-2", "farmer"),
            consent=stored,
        )



def test_revocation_before_prepared_attempt_dispatch_prevents_provider_call():
    actor = ActorIdentity("farmer-1", "farmer")
    consent = grant(actor_id=actor.actor_id)
    store = InMemoryConsentRepository()
    store.create(consent)

    request_action = action()
    route = ActionRouter().route_action(request_action)
    transaction_id = f"txn:{uuid4().hex}"
    attempt_id = f"attempt:{uuid4().hex}"
    now = datetime.now(timezone.utc)
    transactions = InMemoryTransactionRepository()
    transactions.create(
        ServiceTransaction(
            transaction_id=transaction_id,
            idempotency_key=f"dispatch:{uuid4().hex}",
            request_fingerprint=f"request:{uuid4().hex}",
            action_id=request_action.id,
            provider_id="lab-01",
            status="submitted",
        )
    )
    transactions.create_attempt(
        ExecutionAttempt(
            attempt_id=attempt_id,
            transaction_id=transaction_id,
            attempt_number=1,
            provider_id="lab-01",
            status="ready",
            created_at=now,
            updated_at=now,
        )
    )
    adapter = MockServiceAdapter("soil_test", provider_id="lab-01")
    gateway = ServiceExecutionGateway(
        [adapter],
        transaction_repository=transactions,
        consent_repository=store,
    )
    execution_request = ExecutionRequest(
        action=request_action,
        route=route,
        actor=actor,
        consent=consent,
        idempotency_key=f"dispatch:{uuid4().hex}",
        provider_id="lab-01",
        transaction_id=transaction_id,
        attempt_id=attempt_id,
    )

    store.revoke(consent.consent_id, actor_id=actor.actor_id)
    with pytest.raises(ExecutionError, match="consent is not active"):
        gateway.dispatch_ready_attempt(
            attempt_id=attempt_id,
            request=execution_request,
        )

    assert transactions.list_attempts(transaction_id)[0].status == "ready"
