from dataclasses import dataclass, field
from datetime import datetime, timezone

from ofi.domain.models import FarmSnapshot, Observation
from ofi.providers.base import EvidenceProvider
from ofi.twin.repository import FarmTwinRepository


@dataclass(frozen=True)
class ProviderIngestionResult:
    provider: str
    observations: tuple[Observation, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class IngestionReport:
    farm_id: str
    started_at: datetime
    completed_at: datetime
    results: tuple[ProviderIngestionResult, ...] = ()

    @property
    def observations(self) -> list[Observation]:
        return [
            observation
            for result in self.results
            for observation in result.observations
        ]

    @property
    def failures(self) -> list[ProviderIngestionResult]:
        return [result for result in self.results if result.error is not None]

    @property
    def successful_providers(self) -> list[str]:
        return [result.provider for result in self.results if result.error is None]


class EvidenceIngestionService:
    """Run evidence providers and persist successful observations.

    Provider failure is isolated: one unavailable upstream source must not
    prevent other evidence sources from updating the farm twin.
    """

    def __init__(
        self,
        repository: FarmTwinRepository,
        providers: list[EvidenceProvider],
    ):
        self.repository = repository
        self.providers = providers

    def ingest(
        self,
        farm_id: str,
        *,
        as_of: datetime | None = None,
    ) -> IngestionReport:
        started = datetime.now(timezone.utc)
        snapshot = self.repository.snapshot(farm_id, as_of)
        results: list[ProviderIngestionResult] = []

        for provider in self.providers:
            try:
                observations = provider.collect(snapshot)
                for observation in observations:
                    self.repository.add_observation(farm_id, observation)
                results.append(ProviderIngestionResult(
                    provider=provider.name,
                    observations=tuple(observations),
                ))
            except Exception as exc:
                results.append(ProviderIngestionResult(
                    provider=provider.name,
                    error=f"{type(exc).__name__}: {exc}",
                ))

        return IngestionReport(
            farm_id=farm_id,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            results=tuple(results),
        )
