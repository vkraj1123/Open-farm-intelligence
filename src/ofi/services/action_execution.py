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
    provider_id: str
    status: str
    actor_id: str
    external_reference: str | None
    transaction_id: str
    recorded_at: datetime


class ActionExecutionService:
    """Execute a planned action through a selected provider."""

    def __init__(self, gateway: ServiceExecutionGateway):
        self.gateway = gateway

    def execute(
        self,
        *,
        action: ActionRequest,
        actor: ActorIdentity,
        consent: ConsentGrant | None = None,
        provider_id: str | None = None,
    ) -> tuple[ExecutionReceipt, ExecutionEvent]:
        route = self._route(action)
        receipt = self.gateway.submit(
            action=action,
            route=route,
            actor=actor,
            consent=consent,
            provider_id=provider_id,
        )
        return receipt, ExecutionEvent(
            action_id=action.id,
            service=action.service,
            provider_id=receipt.provider_id,
            status=receipt.status,
            actor_id=actor.actor_id,
            external_reference=receipt.external_reference,
            transaction_id=receipt.transaction_id,
            recorded_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _route(action: ActionRequest):
        from ofi.services.action_router import ActionRouter
        return ActionRouter().route_action(action)
