from ofi.domain.models import Decision
from ofi.services.action_planning import ActionPlanningService


def test_action_plan_is_explicit_and_executable_later():
    plan = ActionPlanningService().plan(
        case_id="case-1",
        farm_id="farm-1",
        decision=Decision(
            action="REQUEST_TEST",
            confidence=0.72,
            rationale="Need verification.",
            services=["soil_test"],
        ),
    )
    assert plan.requires_external_execution
    assert plan.requests[0].status == "planned"
    assert plan.requests[0].service == "soil_test"
