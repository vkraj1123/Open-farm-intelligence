from datetime import datetime, timedelta, timezone

from ofi.services.action_execution import ActionExecutionService
from ofi.services.action_router import ActionRequest
from ofi.services.execution_gateway import (
    ActorIdentity,
    ConsentGrant,
    MockServiceAdapter,
    ServiceExecutionGateway,
)


def test_action_execution_returns_auditable_event():
    action = ActionRequest(
        id="case-1-action-1",
        case_id="case-1",
        farm_id="farm-1",
        action="REQUEST_TEST",
        service="soil_test",
        confidence=0.8,
        rationale="Verify soil condition.",
    )
    service = ActionExecutionService(
        ServiceExecutionGateway([MockServiceAdapter("soil_test")])
    )
    receipt, event = service.execute(
        action=action,
        actor=ActorIdentity("farmer-1", "farmer"),
        consent=ConsentGrant(
            "farmer-1", "soil_test", "granted",
            datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        ),
    )
    assert receipt.action_id == action.id
    assert event.external_reference == receipt.external_reference
    assert event.actor_id == "farmer-1"
