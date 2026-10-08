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
        request_fingerprint="fingerprint-1",
        action_id=action().id,
        provider_id="lab-01",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = ServiceTransaction(
        transaction_id="txn-new",
        idempotency_key="attempt-2",
        request_fingerprint="fingerprint-2",
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
