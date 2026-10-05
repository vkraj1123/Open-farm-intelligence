from abc import ABC, abstractmethod
from typing import Any

from ofi.domain.models import FarmSnapshot, Observation


class EvidenceProvider(ABC):
    """Adapter boundary between an external evidence source and OFI."""

    name: str
    capabilities: frozenset[str] = frozenset()

    @abstractmethod
    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        raise NotImplementedError


class ProviderRegistry:
    def __init__(self, providers: list[EvidenceProvider] | None = None):
        self._providers: dict[str, EvidenceProvider] = {}
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: EvidenceProvider) -> None:
        if provider.name in self._providers:
            raise ValueError(f"provider already registered: {provider.name}")
        self._providers[provider.name] = provider

    def get(self, name: str) -> EvidenceProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"provider not registered: {name}") from exc

    def providers_for(self, capability: str) -> list[EvidenceProvider]:
        return [
            provider for provider in self._providers.values()
            if capability in provider.capabilities
        ]

    def collect(self, snapshot: FarmSnapshot) -> list[Observation]:
        observations: list[Observation] = []
        for provider in self._providers.values():
            observations.extend(provider.collect(snapshot))
        return observations


class AgricultureProvider(ABC):
    """Network/service adapter retained for VISTAAR-style APIs."""

    @abstractmethod
    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def init(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
