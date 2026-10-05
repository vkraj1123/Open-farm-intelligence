from dataclasses import dataclass
from datetime import datetime, timezone
from math import cos, radians
from typing import Iterable

from ofi.domain.models import FarmCase, GeoPoint, Observation


@dataclass(frozen=True)
class Alignment:
    observation_id: str
    spatial_match: float
    temporal_match: float
    unit_match: float
    usable: bool
    reasons: tuple[str, ...] = ()


def temporal_match(a: datetime, b: datetime, window_hours: float) -> float:
    delta = abs((a.astimezone(timezone.utc) - b.astimezone(timezone.utc)).total_seconds()) / 3600
    return max(0.0, 1.0 - delta / max(window_hours, 0.001))


def spatial_distance_km(a: GeoPoint, b: GeoPoint) -> float:
    # Equirectangular approximation is sufficient for evidence screening.
    lat = radians((a.latitude + b.latitude) / 2)
    dx = radians(b.longitude - a.longitude) * cos(lat)
    dy = radians(b.latitude - a.latitude)
    return 6371.0 * (dx * dx + dy * dy) ** 0.5


def spatial_match(a: Observation, b: Observation, max_distance_km: float = 5.0) -> float:
    if a.location is None or b.location is None:
        return 1.0 if a.spatial_scope == b.spatial_scope else 0.7
    distance = spatial_distance_km(a.location, b.location)
    return max(0.0, 1.0 - distance / max(max_distance_km, 0.001))


def unit_match(a: Observation, b: Observation) -> float:
    if not a.unit or not b.unit:
        return 1.0
    return 1.0 if a.unit == b.unit else 0.0


def align(a: Observation, b: Observation, *, time_window_hours: float = 72, max_distance_km: float = 5.0) -> Alignment:
    sm = spatial_match(a, b, max_distance_km)
    tm = temporal_match(a.timestamp, b.timestamp, time_window_hours)
    um = unit_match(a, b)
    reasons: list[str] = []
    if sm < 0.5:
        reasons.append("spatial_mismatch")
    if tm < 0.5:
        reasons.append("temporal_mismatch")
    if um < 1.0:
        reasons.append("unit_mismatch")
    return Alignment(
        observation_id=b.id,
        spatial_match=round(sm, 3),
        temporal_match=round(tm, 3),
        unit_match=round(um, 3),
        usable=not reasons,
        reasons=tuple(reasons),
    )


def comparable_pairs(observations: Iterable[Observation], *, kind: str, field: str) -> list[tuple[Observation, Observation, Alignment]]:
    selected = [o for o in observations if o.kind == kind and field in o.value]
    result: list[tuple[Observation, Observation, Alignment]] = []
    for index, left in enumerate(selected):
        for right in selected[index + 1:]:
            alignment = align(left, right)
            if alignment.usable:
                result.append((left, right, alignment))
    return result


def alignment_flags(case: FarmCase) -> list[str]:
    flags: list[str] = []
    checks = (
        ("weather", "temperature_c"),
        ("weather", "rainfall_mm_last_7d"),
        ("satellite", "ndvi"),
        ("soil", "moisture_pct"),
    )
    for kind, field in checks:
        observations = [o for o in case.observations if o.kind == kind and field in o.value]
        for index, left in enumerate(observations):
            for right in observations[index + 1:]:
                result = align(left, right)
                flags.extend(
                    f"{kind}.{field}:{reason}:{left.id}:{right.id}"
                    for reason in result.reasons
                )
    return sorted(set(flags))
