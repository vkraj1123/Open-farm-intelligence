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


def test_execution_requires_authentication():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(),
            route=route(),
            actor=ActorIdentity("u1", "farmer", authenticated=False),
            consent=ConsentGrant(
                "u1", "soil_test", "granted",
                datetime.now(timezone.utc),
            ),
        )


def test_execution_requires_active_consent_for_service():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(),
            route=route(),
            actor=ActorIdentity("u1", "farmer"),
        )


def test_expired_consent_is_rejected():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(),
            route=route(),
            actor=ActorIdentity("u1", "farmer"),
            consent=ConsentGrant(
                "u1", "soil_test", "granted",
                datetime.now(timezone.utc) - timedelta(days=2),
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
        )


def test_mock_adapter_returns_receipt():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    receipt = gateway.submit(
        action=action(),
        route=route(),
        actor=ActorIdentity("u1", "farmer"),
        consent=ConsentGrant(
            "u1", "soil_test", "granted",
            datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
    )
    assert receipt.status == "submitted"
    assert receipt.external_reference == "mock:case-1-action-1"


def test_unregistered_service_is_not_silently_executed():
    gateway = ServiceExecutionGateway()
    with pytest.raises(ExecutionError):
        gateway.submit(
            action=action(),
            route=route(),
            actor=ActorIdentity("u1", "farmer"),
            consent=ConsentGrant(
                "u1", "soil_test", "granted",
                datetime.now(timezone.utc),
            ),
        )
