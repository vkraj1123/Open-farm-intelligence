from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Literal


ProviderStatus = Literal["active", "inactive"]
MatchReason = Literal["capability", "geography", "language", "availability"]


@dataclass(frozen=True)
class ServiceCapability:
    service: str
    capability: str
    action_types: frozenset[str] = frozenset()
    regions: frozenset[str] = frozenset()
    languages: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ServiceProvider:
    provider_id: str
    name: str
    capabilities: tuple[ServiceCapability, ...]
    status: ProviderStatus = "active"
    metadata: dict = field(default_factory=dict)

    def supports(
        self,
        *,
        service: str,
        capability: str,
        action: str | None = None,
        region: str | None = None,
        language: str | None = None,
    ) -> bool:
        if self.status != "active":
            return False
        for item in self.capabilities:
            if item.service != service or item.capability != capability:
                continue
            if item.action_types and action not in item.action_types:
                continue
            if item.regions and region not in item.regions:
                continue
            if item.languages and language not in item.languages:
                continue
            return True
        return False


@dataclass(frozen=True)
class ProviderMatch:
    provider_id: str
    service: str
    capability: str
    reasons: tuple[MatchReason, ...]


class ServiceDirectory:
    """Provider-neutral discovery registry.

    This is intentionally local and deterministic. A later adapter can back
    discovery with VISTAAR/Beckn or another institutional directory.
    """

    def __init__(self, providers: Iterable[ServiceProvider] = ()):
        self._providers = {provider.provider_id: provider for provider in providers}

    def register(self, provider: ServiceProvider) -> None:
        if provider.provider_id in self._providers:
            raise ValueError(f"provider already registered: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def discover(
        self,
        *,
        service: str,
        capability: str,
        action: str | None = None,
        region: str | None = None,
        language: str | None = None,
    ) -> list[ProviderMatch]:
        matches = []
        for provider in self._providers.values():
            if not provider.supports(
                service=service,
                capability=capability,
                action=action,
                region=region,
                language=language,
            ):
                continue
            reasons: list[MatchReason] = ["capability"]
            if region:
                reasons.append("geography")
            if language:
                reasons.append("language")
            matches.append(ProviderMatch(
                provider_id=provider.provider_id,
                service=service,
                capability=capability,
                reasons=tuple(reasons),
            ))
        return matches


@dataclass(frozen=True)
class ServiceRequest:
    request_id: str
    action_id: str
    farm_id: str
    service: str
    capability: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    constraints: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceResponse:
    request_id: str
    provider_id: str
    status: Literal["accepted", "rejected", "completed", "failed"]
    external_reference: str | None = None
    message: str = ""
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
