from dataclasses import dataclass
from datetime import datetime, timezone

from ofi.domain.models import FarmSnapshot
from ofi.intelligence.freshness import EvidenceFreshness, freshness_report
from ofi.providers.base import EvidenceProvider
from ofi.services.ingestion import EvidenceIngestionService
from ofi.twin.repository import FarmTwinRepository


@dataclass(frozen=True)
class RefreshPlan:
    farm_id: str
    generated_at: datetime
    due: tuple[str, ...]

    @property
    def is_current(self) -> bool:
        return not self.due


class FarmEvidenceCoordinator:
    """Coordinates evidence freshness with provider ingestion.

    It deliberately does not schedule itself; a worker, cron, queue, or
    event system can call refresh_due() at the desired cadence.
    """

    def __init__(
        self,
        repository: FarmTwinRepository,
        providers: list[EvidenceProvider],
    ):
        self.repository = repository
        self.ingestion = EvidenceIngestionService(repository, providers)

    def freshness(self, farm_id: str, *, as_of: datetime | None = None) -> list[EvidenceFreshness]:
        snapshot = self.repository.snapshot(farm_id, as_of)
        return freshness_report(snapshot.recent_observations, as_of=snapshot.as_of)

    def plan(self, farm_id: str, *, as_of: datetime | None = None) -> RefreshPlan:
        moment = as_of or datetime.now(timezone.utc)
        states = self.freshness(farm_id, as_of=moment)
        return RefreshPlan(
            farm_id=farm_id,
            generated_at=moment.astimezone(timezone.utc),
            due=tuple(state.kind for state in states if state.due),
        )

    def refresh_due(self, farm_id: str, *, as_of: datetime | None = None):
        plan = self.plan(farm_id, as_of=as_of)
        if plan.is_current:
            return None, plan
        report = self.ingestion.ingest(farm_id, as_of=as_of)
        return report, plan
