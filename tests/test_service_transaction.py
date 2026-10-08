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
from ofi.services.service_transaction import ServiceTransaction, action_status_for_transaction


def action():
    return ActionRequest(
        id="case-2-action-1",
        case_id="case-2",
        farm_id="farm-2",
        action="REQUEST_TEST",
        service="soil_test",
        confidence=0.8,
        rationale="Verify soil condition.",
    )


def test_duplicate_submission_is_idempotent():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    kwargs = dict(
        action=action(),
        route=ActionRouter().route_action(action()),
        actor=ActorIdentity("u1", "farmer"),
        consent=ConsentGrant(
            "u1", "soil_test", "granted", datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        idempotency_key="same-request",
    )
    first = gateway.submit(**kwargs)
    second = gateway.submit(**kwargs)
    assert second.transaction_id == first.transaction_id
    assert second.external_reference == first.external_reference
    assert second.message == "Replayed idempotent submission."


def test_transaction_status_transitions_are_guarded():
    tx = ServiceTransaction(
        transaction_id="txn-1",
        idempotency_key="key-1",
        action_id="action-1",
        provider_id="provider-1",
    )
    tx.transition("submitted")
    tx.transition("accepted")
    tx.transition("in_progress")
    tx.transition("completed")
    assert tx.status == "completed"
    with pytest.raises(ValueError):
        tx.transition("failed")


def test_gateway_can_update_external_transaction_status():
    gateway = ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    receipt = gateway.submit(
        action=action(),
        route=ActionRouter().route_action(action()),
        actor=ActorIdentity("u1", "farmer"),
        consent=ConsentGrant(
            "u1", "soil_test", "granted", datetime.now(timezone.utc),
        ),
    )
    tx = gateway.update_status(
        receipt.transaction_id,
        "accepted",
        external_reference="LAB-123",
        message="Provider accepted the request.",
    )
    assert tx.status == "accepted"
    assert tx.external_reference == "LAB-123"


def test_transaction_projects_to_action_lifecycle():
    tx = ServiceTransaction(transaction_id="txn-2", idempotency_key="key-2", action_id="a-2", provider_id="p-1")
    assert tx.action_status == "planned"
    tx.transition("submitted")
    assert tx.action_status == "routed"
    tx.transition("accepted")
    assert tx.action_status == "accepted"
    tx.transition("in_progress")
    assert tx.action_status == "in_progress"
    tx.transition("completed")
    assert tx.action_status == "completed"


def test_terminal_transaction_projection_is_failed_for_rejection():
    assert action_status_for_transaction("rejected") == "failed"
    assert action_status_for_transaction("cancelled") == "cancelled"
