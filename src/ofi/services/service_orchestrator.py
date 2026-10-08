from dataclasses import dataclass

from ofi.services.action_router import ActionRequest, ActionRouter
from ofi.services.provider_selection import ProviderSelectionPolicy
from ofi.services.service_directory import ServiceDirectory, ServiceRequest


@dataclass(frozen=True)
class ServiceSelection:
    request: ServiceRequest
    provider_id: str
    rationale: str


class ServiceOrchestrator:
    """Resolve a planned action to an available institutional provider."""

    def __init__(
        self,
        directory: ServiceDirectory,
        *,
        selection_policy: ProviderSelectionPolicy | None = None,
    ):
        self.directory = directory
        self.selection_policy = selection_policy or ProviderSelectionPolicy()

    def select(
        self,
        action: ActionRequest,
        *,
        region: str | None = None,
        language: str | None = None,
    ) -> ServiceSelection:
        route = ActionRouter().route_action(action)
        request = ServiceRequest(
            request_id=f"req:{action.id}",
            action_id=action.id,
            farm_id=action.farm_id,
            service=route.service,
            capability=route.channel,
            constraints={
                "region": region,
                "language": language,
                "urgency": action.urgency,
            },
        )
        matches = self.directory.discover(
            service=route.service,
            capability=route.channel,
            action=action.action,
            region=region,
            language=language,
        )
        if not matches:
            raise LookupError(
                f"no provider for service={route.service}, capability={route.channel}"
            )

        match, policy_rationale = self.selection_policy.select(
            self.directory,
            matches,
            region=region,
            language=language,
            urgency=action.urgency,
        )
        return ServiceSelection(
            request=request,
            provider_id=match.provider_id,
            rationale=policy_rationale,
        )
