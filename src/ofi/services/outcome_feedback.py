from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from ofi.services.action_router import ActionRequest


Effectiveness = Literal["positive", "neutral", "negative", "unknown"]


@dataclass(frozen=True)
class ActionOutcome:
    action_id: str
    case_id: str
    farm_id: str
    effectiveness: Effectiveness
    observed_at: datetime
    notes: str = ""
    evidence_ids: tuple[str, ...] = ()
    attribution_confidence: float = 0.0

    def __post_init__(self):
        if not 0.0 <= self.attribution_confidence <= 1.0:
            raise ValueError("attribution_confidence must be between 0 and 1")


@dataclass(frozen=True)
class LearningSignal:
    action_id: str
    service: str
    signal: int
    weight: float
    reason: str


class OutcomeFeedbackService:
    """Turn observed action outcomes into explicit, bounded learning signals.

    This is not model training. It creates evaluation signals that can later
    feed policy/model evaluation while preserving uncertainty and attribution.
    """

    SIGNALS = {
        "positive": 1,
        "neutral": 0,
        "negative": -1,
        "unknown": 0,
    }

    def evaluate(
        self,
        action: ActionRequest,
        outcome: ActionOutcome,
    ) -> LearningSignal:
        if action.id != outcome.action_id:
            raise ValueError("action and outcome IDs do not match")
        signal = self.SIGNALS[outcome.effectiveness]
        return LearningSignal(
            action_id=action.id,
            service=action.service,
            signal=signal,
            weight=outcome.attribution_confidence,
            reason=outcome.notes or outcome.effectiveness,
        )

    def compare(
        self,
        outcomes: list[ActionOutcome],
    ) -> dict[str, float]:
        """Return service-level weighted effectiveness, not a causal estimate."""
        totals: dict[str, float] = {}
        weights: dict[str, float] = {}
        for outcome in outcomes:
            signal = self.SIGNALS[outcome.effectiveness]
            service = outcome.action_id.split("-action-", 1)[0]
            totals[service] = totals.get(service, 0.0) + signal * outcome.attribution_confidence
            weights[service] = weights.get(service, 0.0) + outcome.attribution_confidence
        return {
            service: totals[service] / weights[service]
            for service in totals
            if weights[service] > 0
        }
