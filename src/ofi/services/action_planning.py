from dataclasses import dataclass
from datetime import datetime, timezone

from ofi.domain.models import Decision
from ofi.services.action_router import ActionRequest, ActionRouter


@dataclass(frozen=True)
class ActionPlan:
    case_id: str
    farm_id: str
    created_at: datetime
    requests: tuple[ActionRequest, ...]

    @property
    def requires_external_execution(self) -> bool:
        return bool(self.requests)


class ActionPlanningService:
    def __init__(self, router: ActionRouter | None = None):
        self.router = router or ActionRouter()

    def plan(
        self,
        *,
        case_id: str,
        farm_id: str,
        decision: Decision,
    ) -> ActionPlan:
        requests = self.router.route(
            case_id=case_id,
            farm_id=farm_id,
            decision=decision,
        )
        return ActionPlan(
            case_id=case_id,
            farm_id=farm_id,
            created_at=datetime.now(timezone.utc),
            requests=tuple(requests),
        )
