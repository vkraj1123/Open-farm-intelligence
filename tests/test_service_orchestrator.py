from ofi.services.action_router import ActionRequest
from ofi.services.service_directory import (
    ServiceCapability,
    ServiceDirectory,
    ServiceProvider,
)
from ofi.services.service_orchestrator import ServiceOrchestrator


def test_service_orchestrator_builds_traceable_request():
    directory = ServiceDirectory([
        ServiceProvider(
            provider_id="kvk-01",
            name="KVK",
            capabilities=(
                ServiceCapability(
                    service="KVK",
                    capability="agriculture_extension",
                    action_types=frozenset({"ESCALATE_EXPERT"}),
                    regions=frozenset({"Balotra"}),
                ),
            ),
        )
    ])
    action = ActionRequest(
        id="case-1-action-1",
        case_id="case-1",
        farm_id="farm-1",
        action="ESCALATE_EXPERT",
        service="KVK",
        confidence=0.8,
        rationale="Needs expert review.",
    )
    selection = ServiceOrchestrator(directory).select(
        action, region="Balotra"
    )
    assert selection.provider_id == "kvk-01"
    assert selection.request.request_id == "req:case-1-action-1"
    assert selection.request.farm_id == "farm-1"
