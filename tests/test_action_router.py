from datetime import datetime, timezone

from ofi.domain.models import Decision
from ofi.services.action_router import ActionRouter


def test_routes_soil_test_as_diagnostic_action():
    decision = Decision(
        action="REQUEST_TEST",
        confidence=0.7,
        rationale="Water stress is suggestive.",
        services=["soil_test"],
    )
    request = ActionRouter().route(
        case_id="case-1", farm_id="farm-1", decision=decision
    )[0]
    assert request.service == "soil_test"
    assert request.status == "planned"
    assert ActionRouter().route_action(request).channel == "soil_testing"


def test_routes_expert_escalation_to_human_channels():
    decision = Decision(
        action="ESCALATE_EXPERT",
        confidence=0.6,
        rationale="Evidence is weak.",
    )
    requests = ActionRouter().route(
        case_id="case-1", farm_id="farm-1", decision=decision
    )
    assert {item.service for item in requests} == {"KVK", "agriculture_extension"}
    assert all(ActionRouter().route_action(item).capability == "expert" for item in requests)


def test_advice_without_service_does_not_create_fake_external_action():
    decision = Decision(
        action="ADVISE",
        confidence=0.9,
        rationale="Evidence is strong.",
    )
    assert ActionRouter().route(
        case_id="case-1", farm_id="farm-1", decision=decision
    ) == []
