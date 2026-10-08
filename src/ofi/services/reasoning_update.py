from dataclasses import dataclass
from datetime import datetime, timezone

from ofi.domain.models import FarmCase, ReasoningResult
from ofi.intelligence.orchestrator import Orchestrator
from ofi.services.ingestion import IngestionReport


@dataclass(frozen=True)
class DecisionChange:
    changed: bool
    previous: str | None
    current: str
    confidence_delta: float
    reason: str


@dataclass(frozen=True)
class ReasoningUpdate:
    reasoning: ReasoningResult
    change: DecisionChange
    generated_at: datetime


class ReasoningUpdateService:
    """Re-run farm reasoning after evidence changes and detect decision shifts."""

    def __init__(self, orchestrator: Orchestrator | None = None):
        self.orchestrator = orchestrator or Orchestrator()

    def update(self, case: FarmCase, previous: ReasoningResult | None) -> ReasoningUpdate:
        reasoning = self.orchestrator.reason(case)
        previous_action = previous.decision.action if previous else None
        current_action = reasoning.decision.action
        previous_confidence = previous.decision.confidence if previous else 0.0
        delta = reasoning.decision.confidence - previous_confidence

        changed = (
            previous is None
            or previous_action != current_action
            or abs(delta) >= 0.15
        )
        if previous is None:
            reason = "Initial reasoning produced from available evidence."
        elif previous_action != current_action:
            reason = f"Decision changed from {previous_action} to {current_action}."
        elif abs(delta) >= 0.15:
            reason = f"Decision confidence changed by {delta:+.2f}."
        else:
            reason = "New evidence did not materially change the decision."

        return ReasoningUpdate(
            reasoning=reasoning,
            change=DecisionChange(
                changed=changed,
                previous=previous_action,
                current=current_action,
                confidence_delta=delta,
                reason=reason,
            ),
            generated_at=datetime.now(timezone.utc),
        )

    def after_ingestion(
        self,
        case: FarmCase,
        previous: ReasoningResult | None,
        report: IngestionReport,
    ) -> ReasoningUpdate:
        update = self.update(case, previous)
        # Keep ingestion metadata out of the reasoning model itself; callers
        # can use the report and change record to trigger downstream actions.
        return update
