from dataclasses import dataclass

from ofi.domain.models import Hypothesis
from ofi.services.experience_memory import ExperienceMatch


@dataclass(frozen=True)
class ExperienceSummary:
    service: str
    sample_size: int
    weighted_effectiveness: float
    average_similarity: float
    confidence: float
    caveat: str


def summarize_experience(matches: list[ExperienceMatch]) -> list[ExperienceSummary]:
    grouped: dict[str, list[ExperienceMatch]] = {}
    for match in matches:
        grouped.setdefault(match.record.service, []).append(match)

    summaries: list[ExperienceSummary] = []
    for service, items in grouped.items():
        weights = [item.similarity * item.record.attribution_confidence for item in items]
        total = sum(weights)
        effectiveness = (
            sum(item.weighted_signal for item in items) / total if total else 0.0
        )
        similarity = sum(item.similarity for item in items) / len(items)
        confidence = min(1.0, total / 3.0)
        summaries.append(ExperienceSummary(
            service=service,
            sample_size=len(items),
            weighted_effectiveness=round(effectiveness, 3),
            average_similarity=round(similarity, 3),
            confidence=round(confidence, 3),
            caveat="Empirical association only; not a causal estimate.",
        ))
    return sorted(summaries, key=lambda item: item.weighted_effectiveness, reverse=True)
