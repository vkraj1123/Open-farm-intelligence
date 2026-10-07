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
