from dataclasses import dataclass
from datetime import datetime, timezone

from ofi.services.execution_gateway import (
    ActorIdentity,
    ConsentGrant,
    ExecutionReceipt,
    ServiceExecutionGateway,
)
from ofi.services.action_router import ActionRequest


@dataclass(frozen=True)
class ExecutionEvent:
    action_id: str
    service: str
    status: str
    actor_id: str
    external_reference: str | None
    recorded_at: datetime


class ActionExecutionService:
    """Execute planned actions and expose an auditable receipt."""

    def __init__(self, gateway: ServiceExecutionGateway):
        self.gateway = gateway

    def execute(
        self,
        *,
        action: ActionRequest,
        actor: ActorIdentity,
        consent: ConsentGrant | None = None,
    ) -> tuple[ExecutionReceipt, ExecutionEvent]:
        route = self._route(action)
        receipt = self.gateway.submit(
            action=action,
            route=route,
            actor=actor,
            consent=consent,
        )
        return receipt, ExecutionEvent(
            action_id=action.id,
            service=action.service,
            status=receipt.status,
            actor_id=actor.actor_id,
            external_reference=receipt.external_reference,
            recorded_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _route(action: ActionRequest):
        from ofi.services.action_router import ActionRouter
        return ActionRouter().route_action(action)
