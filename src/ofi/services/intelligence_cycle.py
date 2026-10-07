from dataclasses import dataclass
from datetime import datetime, timezone

from ofi.domain.models import FarmCase, ReasoningResult
from ofi.services.ingestion import IngestionReport
from ofi.services.reasoning_update import ReasoningUpdate, ReasoningUpdateService


@dataclass(frozen=True)
class IntelligenceCycle:
    """One evidence-to-decision transition for a farm case."""

    ingestion: IngestionReport
    reasoning: ReasoningUpdate
    generated_at: datetime

    @property
    def decision_changed(self) -> bool:
        return self.reasoning.change.changed


class IntelligenceCycleService:
    def __init__(self, reasoning: ReasoningUpdateService | None = None):
        self.reasoning = reasoning or ReasoningUpdateService()

    def process(
        self,
        case: FarmCase,
        previous: ReasoningResult | None,
        ingestion: IngestionReport,
    ) -> IntelligenceCycle:
        update = self.reasoning.after_ingestion(case, previous, ingestion)
        return IntelligenceCycle(
            ingestion=ingestion,
            reasoning=update,
            generated_at=datetime.now(timezone.utc),
        )
