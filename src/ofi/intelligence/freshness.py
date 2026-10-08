from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ofi.domain.models import Observation


DEFAULT_FRESHNESS_DAYS = {
    "weather": 3,
    "sensor": 7,
    "soil": 60,
    "satellite": 14,
    "image": 7,
    "farmer_report": 7,
    "market": 2,
    "model": 7,
    "expert": 30,
}


@dataclass(frozen=True)
class EvidenceFreshness:
    kind: str
    latest: Observation | None
    age_hours: float | None
    fresh: bool
    due: bool
    max_age_days: int

    @property
    def state(self) -> str:
        if self.latest is None:
            return "missing"
        if self.fresh:
            return "fresh"
        return "stale"


def freshness_for(
    observations: list[Observation],
    *,
    kind: str,
    as_of: datetime | None = None,
    max_age_days: int | None = None,
) -> EvidenceFreshness:
    moment = as_of or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    moment = moment.astimezone(timezone.utc)

    window = max_age_days or DEFAULT_FRESHNESS_DAYS.get(kind, 7)
    candidates = [
        item for item in observations
        if item.kind == kind and item.timestamp <= moment
    ]
    latest = max(candidates, key=lambda item: item.timestamp, default=None)

    if latest is None:
        return EvidenceFreshness(kind, None, None, False, True, window)

    age = round(max(0.0, (moment - latest.timestamp).total_seconds() / 3600), 6)
    fresh = age <= window * 24
    return EvidenceFreshness(
        kind=kind,
        latest=latest,
        age_hours=age,
        fresh=fresh,
        due=not fresh,
        max_age_days=window,
    )


def freshness_report(
    observations: list[Observation],
    *,
    as_of: datetime | None = None,
    kinds: tuple[str, ...] = (
        "weather", "satellite", "soil", "sensor", "market"
    ),
) -> list[EvidenceFreshness]:
    return [
        freshness_for(observations, kind=kind, as_of=as_of)
        for kind in kinds
    ]
