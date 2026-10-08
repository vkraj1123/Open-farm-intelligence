from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from ofi.domain.models import Decision


ActionStatus = Literal["planned", "routed", "accepted", "in_progress", "completed", "cancelled", "failed"]
Urgency = Literal["routine", "soon", "urgent"]


@dataclass(frozen=True)
class ActionRequest:
    id: str
    case_id: str
    farm_id: str
    action: str
    service: str
    confidence: float
    rationale: str
    urgency: Urgency = "routine"
    status: ActionStatus = "planned"
    requested_at: datetime | None = None
    metadata: dict | None = None

    def __post_init__(self):
        if self.requested_at is None:
            object.__setattr__(self, "requested_at", datetime.now(timezone.utc))


@dataclass(frozen=True)
class ActionRoute:
    action_id: str
    service: str
    capability: str
    channel: str
    metadata: dict


class ActionRouter:
    """Translate decisions into explicit, externally-routable actions.

    Routing is deliberately declarative. No provider/API is called here;
    adapters can later connect these routes to VISTAAR, KVKs, FPOs, service
    providers, or human workflows.
    """

    SERVICE_CAPABILITIES = {
        "soil_test": ("diagnostic", "soil_testing"),
        "crop_disease_diagnosis": ("diagnostic", "crop_disease"),
        "KVK": ("expert", "agriculture_extension"),
        "agriculture_extension": ("expert", "agriculture_extension"),
        "evidence_verification": ("verification", "evidence_verification"),
    }

    def route(
        self,
        *,
        case_id: str,
        farm_id: str,
        decision: Decision,
    ) -> list[ActionRequest]:
        requests: list[ActionRequest] = []

        if decision.action in {"ASK_FARMER", "ADVISE"} and not decision.services:
            return requests

        services = decision.services or self._default_services(decision)
        urgency = "urgent" if decision.confidence >= 0.85 else "soon"

        for index, service in enumerate(services, start=1):
            requests.append(ActionRequest(
                id=f"{case_id}-action-{index}",
                case_id=case_id,
                farm_id=farm_id,
                action=decision.action,
                service=service,
                confidence=decision.confidence,
                rationale=decision.rationale,
                urgency=urgency,
                metadata={"next_questions": decision.next_questions},
            ))
        return requests

    def route_action(self, request: ActionRequest) -> ActionRoute:
        capability, channel = self.SERVICE_CAPABILITIES.get(
            request.service,
            ("general", "human_review"),
        )
        return ActionRoute(
            action_id=request.id,
            service=request.service,
            capability=capability,
            channel=channel,
            metadata=request.metadata or {},
        )

    @staticmethod
    def _default_services(decision: Decision) -> list[str]:
        if decision.action == "ESCALATE_EXPERT":
            return ["KVK", "agriculture_extension"]
        if decision.action == "REQUEST_TEST":
            return ["evidence_verification"]
        return []
