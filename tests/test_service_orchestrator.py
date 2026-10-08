from ofi.services.action_router import ActionRequest
from ofi.services.service_directory import (
    ServiceCapability,
    ServiceDirectory,
    ServiceProvider,
)
from ofi.services.service_orchestrator import ServiceOrchestrator


def _action() -> ActionRequest:
    return ActionRequest(
        id="case-1-action-1",
        case_id="case-1",
        farm_id="farm-1",
        action="ESCALATE_EXPERT",
        service="KVK",
        confidence=0.8,
        rationale="Needs expert review.",
        urgency="urgent",
    )


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
    selection = ServiceOrchestrator(directory).select(
        _action(), region="Balotra"
    )
    assert selection.provider_id == "kvk-01"
    assert selection.request.request_id == "req:case-1-action-1"
    assert selection.request.farm_id == "farm-1"


def test_selection_prefers_available_healthy_provider():
    directory = ServiceDirectory([
        ServiceProvider(
            provider_id="kvk-busy",
            name="Busy KVK",
            capabilities=(
                ServiceCapability(
                    service="KVK",
                    capability="agriculture_extension",
                    action_types=frozenset({"ESCALATE_EXPERT"}),
                    regions=frozenset({"Balotra"}),
                ),
            ),
            metadata={"availability": "busy", "health_score": 1.0, "trust_score": 0.9},
        ),
        ServiceProvider(
            provider_id="kvk-ready",
            name="Ready KVK",
            capabilities=(
                ServiceCapability(
                    service="KVK",
                    capability="agriculture_extension",
                    action_types=frozenset({"ESCALATE_EXPERT"}),
                    regions=frozenset({"Balotra"}),
                ),
            ),
            metadata={"availability": "available", "health_score": 0.8, "trust_score": 0.8},
        ),
    ])
    selection = ServiceOrchestrator(directory).select(_action(), region="Balotra")
    assert selection.provider_id == "kvk-ready"
    assert "deterministic policy" in selection.rationale


def test_selection_prefers_exact_language_over_generic_provider():
    directory = ServiceDirectory([
        ServiceProvider(
            provider_id="generic",
            name="Generic",
            capabilities=(
                ServiceCapability(
                    service="KVK",
                    capability="agriculture_extension",
                    action_types=frozenset({"ESCALATE_EXPERT"}),
                    regions=frozenset({"Balotra"}),
                ),
            ),
        ),
        ServiceProvider(
            provider_id="hindi",
            name="Hindi",
            capabilities=(
                ServiceCapability(
                    service="KVK",
                    capability="agriculture_extension",
                    action_types=frozenset({"ESCALATE_EXPERT"}),
                    regions=frozenset({"Balotra"}),
                    languages=frozenset({"hi"}),
                ),
            ),
        ),
    ])
    selection = ServiceOrchestrator(directory).select(
        _action(), region="Balotra", language="hi"
    )
    assert selection.provider_id == "hindi"


def test_selection_uses_sla_and_distance_after_context_fit():
    directory = ServiceDirectory([
        ServiceProvider(
            provider_id="slow-near",
            name="Slow Near",
            capabilities=(
                ServiceCapability(
                    service="KVK",
                    capability="agriculture_extension",
                    action_types=frozenset({"ESCALATE_EXPERT"}),
                    regions=frozenset({"Balotra"}),
                    languages=frozenset({"hi"}),
                ),
            ),
            metadata={
                "availability": "available",
                "health_score": 0.9,
                "trust_score": 0.9,
                "sla_minutes": 600,
                "distance_km": 10,
            },
        ),
        ServiceProvider(
            provider_id="fast-far",
            name="Fast Far",
            capabilities=(
                ServiceCapability(
                    service="KVK",
                    capability="agriculture_extension",
                    action_types=frozenset({"ESCALATE_EXPERT"}),
                    regions=frozenset({"Balotra"}),
                    languages=frozenset({"hi"}),
                ),
            ),
            metadata={
                "availability": "available",
                "health_score": 0.9,
                "trust_score": 0.9,
                "sla_minutes": 60,
                "distance_km": 200,
            },
        ),
    ])
    selection = ServiceOrchestrator(directory).select(
        _action(), region="Balotra", language="hi"
    )
    assert selection.provider_id == "fast-far"


def test_selection_is_not_registration_order_when_metadata_differs():
    providers = []
    for provider_id in ("z-provider", "a-provider"):
        providers.append(
            ServiceProvider(
                provider_id=provider_id,
                name=provider_id,
                capabilities=(
                    ServiceCapability(
                        service="KVK",
                        capability="agriculture_extension",
                        action_types=frozenset({"ESCALATE_EXPERT"}),
                        regions=frozenset({"Balotra"}),
                    ),
                ),
                metadata={
                    "availability": "available",
                    "health_score": 0.9,
                    "trust_score": 0.9,
                },
            )
        )
    selection = ServiceOrchestrator(ServiceDirectory(providers)).select(
        _action(), region="Balotra"
    )
    assert selection.provider_id == "a-provider"
