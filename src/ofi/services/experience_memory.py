from dataclasses import dataclass
from datetime import datetime, timezone
from math import exp
from typing import Iterable

from ofi.domain.models import FarmCase, Observation
from ofi.services.action_router import ActionRequest
from ofi.services.outcome_feedback import ActionOutcome


@dataclass(frozen=True)
class ContextSignature:
    """Stable, explainable context used to retrieve comparable experiences."""

    crop: str
    season: str | None
    irrigation_method: str | None
    hypotheses: tuple[str, ...]
    evidence_kinds: tuple[str, ...]
    administrative_area: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ExperienceRecord:
    action_id: str
    service: str
    context: ContextSignature
    effectiveness: str
    outcome_at: datetime
    attribution_confidence: float
    evidence_ids: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class ExperienceMatch:
    record: ExperienceRecord
    similarity: float
    weighted_signal: float


class ExperienceMemory:
    """Append-only empirical memory for comparable farm interventions.

    This is retrieval/evaluation memory, not autonomous model training.
    """

    def __init__(self, records: Iterable[ExperienceRecord] | None = None):
        self._records = list(records or [])

    def add(
        self,
        *,
        action: ActionRequest,
        outcome: ActionOutcome,
        case: FarmCase,
        hypothesis_codes: tuple[str, ...] = (),
    ) -> ExperienceRecord:
        if action.id != outcome.action_id or action.service != outcome.service:
            raise ValueError("action and outcome must refer to the same action")
        context = context_signature(case, hypothesis_codes)
        record = ExperienceRecord(
            action_id=action.id,
            service=action.service,
            context=context,
            effectiveness=outcome.effectiveness,
            outcome_at=outcome.observed_at,
            attribution_confidence=outcome.attribution_confidence,
            evidence_ids=outcome.evidence_ids,
            notes=outcome.notes,
        )
        self._records.append(record)
        return record

    def retrieve(
        self,
        case: FarmCase,
        *,
        hypothesis_codes: tuple[str, ...] = (),
        service: str | None = None,
        limit: int = 10,
        as_of: datetime | None = None,
    ) -> list[ExperienceMatch]:
        target = context_signature(case, hypothesis_codes)
        moment = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        matches: list[ExperienceMatch] = []

        for record in self._records:
            if service and record.service != service:
                continue
            if record.outcome_at > moment:
                continue
            similarity = context_similarity(target, record.context)
            if similarity <= 0:
                continue
            signal = {"positive": 1.0, "neutral": 0.0, "negative": -1.0, "unknown": 0.0}.get(
                record.effectiveness, 0.0
            )
            matches.append(ExperienceMatch(
                record=record,
                similarity=similarity,
                weighted_signal=signal * similarity * record.attribution_confidence,
            ))

        matches.sort(key=lambda item: (item.similarity, item.record.outcome_at), reverse=True)
        return matches[:limit]


def context_signature(case: FarmCase, hypothesis_codes: tuple[str, ...] = ()) -> ContextSignature:
    evidence_kinds = tuple(sorted({item.kind for item in case.observations}))
    area = tuple(sorted(case.farm.parcel.administrative_area.items()))
    return ContextSignature(
        crop=case.farm.crop_cycle.crop,
        season=case.farm.crop_cycle.season,
        irrigation_method=case.farm.crop_cycle.irrigation_method,
        hypotheses=tuple(sorted(hypothesis_codes)),
        evidence_kinds=evidence_kinds,
        administrative_area=area,
    )


def context_similarity(a: ContextSignature, b: ContextSignature) -> float:
    score = 0.0
    weight = 0.0

    def add(match: bool, w: float):
        nonlocal score, weight
        score += w if match else 0.0
        weight += w

    add(a.crop == b.crop, 0.35)
    add(a.season is not None and a.season == b.season, 0.15)
    add(
        a.irrigation_method is not None
        and a.irrigation_method == b.irrigation_method,
        0.15,
    )
    add(bool(set(a.hypotheses) & set(b.hypotheses)), 0.20)
    add(bool(set(a.evidence_kinds) & set(b.evidence_kinds)), 0.05)
    add(bool(set(a.administrative_area) & set(b.administrative_area)), 0.10)
    return score / weight if weight else 0.0
