from datetime import datetime, timedelta, timezone

import pytest

from ofi.services.action_router import ActionRequest, ActionRouter
from ofi.services.execution_gateway import (
    ActorIdentity,
    ConsentGrant,
    ExecutionError,
    MockServiceAdapter,
    ServiceExecutionGateway,
)
from ofi.services.service_directory import ServiceCapability, ServiceDirectory, ServiceProvider
from ofi.services.service_transaction import ServiceTransaction
from ofi.services.transaction_repository import InMemoryTransactionRepository
from ofi.services.service_orchestrator import ServiceOrchestrator


def action():
    return ActionRequest(
        id="case-1-action-1",
        case_id="case-1",
        farm_id="farm-1",
        action="REQUEST_TEST",
        service="soil_test",
        confidence=0.8,
        rationale="Verify soil condition.",
    )


def route():
    return ActionRouter().route_action(action())


def consent():
    return ConsentGrant(
        "u1", "soil_test", "granted", datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )


def test_execution_requires_authentication():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(), route=route(),
            actor=ActorIdentity("u1", "farmer", authenticated=False),
            consent=consent(),
        )


def test_execution_requires_active_consent_for_service():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    with pytest.raises(ExecutionError):
        gateway.submit(action=action(), route=route(), actor=ActorIdentity("u1", "farmer"))


def test_expired_consent_is_rejected():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(), route=route(), actor=ActorIdentity("u1", "farmer"),
            consent=ConsentGrant(
                "u1", "soil_test", "granted",
                datetime.now(timezone.utc) - timedelta(days=2),
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
        )


def test_mock_adapter_returns_provider_aware_receipt():
    gateway = ServiceExecutionGateway([
        MockServiceAdapter("soil_test", provider_id="lab-01")
    ])
    receipt = gateway.submit(
        action=action(), route=route(),
        actor=ActorIdentity("u1", "farmer"),
        consent=consent(),
        provider_id="lab-01",
    )
    assert receipt.status == "submitted"
    assert receipt.provider_id == "lab-01"
    assert receipt.external_reference == "mock:case-1-action-1"


def test_provider_discovery_identity_reaches_execution():
    directory = ServiceDirectory([
        ServiceProvider(
            provider_id="lab-01",
            name="Soil Lab",
            capabilities=(
                ServiceCapability(
                    service="soil_test",
                    capability="soil_testing",
                    action_types=frozenset({"REQUEST_TEST"}),
                ),
            ),
        ),
        ServiceProvider(
            provider_id="lab-02",
            name="Other Soil Lab",
            capabilities=(
                ServiceCapability(
                    service="soil_test",
                    capability="soil_testing",
                    action_types=frozenset({"REQUEST_TEST"}),
                ),
            ),
        ),
    ])
    selection = ServiceOrchestrator(directory).select(action())
    gateway = ServiceExecutionGateway([
        MockServiceAdapter("soil_test", provider_id=selection.provider_id)
    ])
    receipt = gateway.submit(
        action=action(), route=route(),
        actor=ActorIdentity("u1", "farmer"),
        consent=consent(),
        provider_id=selection.provider_id,
    )
    assert receipt.provider_id == "lab-01"


def test_unregistered_service_or_provider_is_not_silently_executed():
    gateway = ServiceExecutionGateway()
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(), route=route(), actor=ActorIdentity("u1", "farmer"),
            consent=consent(), provider_id="missing-provider",
        )


def test_action_status_uses_explicit_transaction_creation_time():
    repo = InMemoryTransactionRepository()
    gateway = ServiceExecutionGateway(
        [MockServiceAdapter("soil_test", provider_id="lab-01")],
        transaction_repository=repo,
    )
    first = ServiceTransaction(
        transaction_id="txn-old",
        idempotency_key="attempt-1",
        action_id=action().id,
        provider_id="lab-01",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = ServiceTransaction(
        transaction_id="txn-new",
        idempotency_key="attempt-2",
        action_id=action().id,
        provider_id="lab-01",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    repo.create(first)
    repo.create(second)
    repo.transition(first.transaction_id, "submitted")
    repo.transition(first.transaction_id, "accepted")
    repo.transition(first.transaction_id, "in_progress")
    repo.transition(first.transaction_id, "completed")
    repo.transition(second.transaction_id, "submitted")

    assert gateway.action_status(action().id) == "routed"


def test_idempotency_key_cannot_be_reused_for_different_request():
    gateway = ServiceExecutionGateway([
        MockServiceAdapter("soil_test", provider_id="lab-01"),
        MockServiceAdapter("soil_test", provider_id="lab-02"),
    ])
    actor = ActorIdentity("u1", "farmer")
    gateway.submit(
        action=action(), route=route(), actor=actor, consent=consent(),
        provider_id="lab-01", idempotency_key="same-key",
    )
    with pytest.raises(ExecutionError, match="idempotency key was reused"):
        gateway.submit(
            action=action(), route=route(), actor=actor, consent=consent(),
            provider_id="lab-02", idempotency_key="same-key",
        )


def test_provider_callback_requires_valid_signature_and_is_idempotent():
    import hashlib
    import hmac

    secret = "callback-secret"
    gateway = ServiceExecutionGateway(
        [MockServiceAdapter("soil_test", provider_id="lab-01")],
        callback_secrets={"lab-01": secret},
    )
    receipt = gateway.submit(
        action=action(), route=route(),
        actor=ActorIdentity("u1", "farmer"), consent=consent(),
        provider_id="lab-01",
    )
    body = b'{"event_id":"evt-1","status":"accepted"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    occurred_at = datetime.now(timezone.utc) + timedelta(seconds=1)

    updated = gateway.apply_provider_callback(
        provider_id="lab-01", event_id="evt-1",
        transaction_id=receipt.transaction_id, status="accepted",
        occurred_at=occurred_at, signature=signature, raw_body=body,
        external_reference="lab-ref-1", message="Lab accepted request.",
    )
    replay = gateway.apply_provider_callback(
        provider_id="lab-01", event_id="evt-1",
        transaction_id=receipt.transaction_id, status="accepted",
        occurred_at=occurred_at, signature=signature, raw_body=body,
        external_reference="lab-ref-1", message="Lab accepted request.",
    )
    assert updated.status == "accepted"
    assert replay.status == "accepted"
    assert len(replay.events) == 2


def test_provider_callback_rejects_bad_signature_and_stale_transition():
    import hashlib
    import hmac

    secret = "callback-secret"
    gateway = ServiceExecutionGateway(
        [MockServiceAdapter("soil_test", provider_id="lab-01")],
        callback_secrets={"lab-01": secret},
    )
    receipt = gateway.submit(
        action=action(), route=route(),
        actor=ActorIdentity("u1", "farmer"), consent=consent(),
        provider_id="lab-01",
    )
    body = b'{"event_id":"evt-bad","status":"accepted"}'
    with pytest.raises(CallbackAuthenticationError):
        gateway.apply_provider_callback(
            provider_id="lab-01", event_id="evt-bad",
            transaction_id=receipt.transaction_id, status="accepted",
            occurred_at=datetime.now(timezone.utc),
            signature="bad", raw_body=body,
        )

    body = b'{"event_id":"evt-2","status":"accepted"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    stale_time = receipt.submitted_at - timedelta(seconds=1)
    result = gateway.apply_provider_callback(
        provider_id="lab-01", event_id="evt-2",
        transaction_id=receipt.transaction_id, status="accepted",
        occurred_at=stale_time, signature=signature, raw_body=body,
    )
    assert result.status == "submitted"
