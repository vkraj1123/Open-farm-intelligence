"""Deterministic alignment of observations before farm reasoning.

This module does not infer agronomic truth. It exposes scope, freshness,
identity and provenance conditions so downstream reasoning can fail safely.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from ofi.domain.models import FarmSnapshot, Observation


@dataclass(frozen=True)
class ObservationAlignment:
    """Machine-readable eligibility assessment for one observation."""

    observation_id: str
    freshness: str
    age_seconds: float | None
    eligible: bool
    issues: tuple[str, ...]
    quality: float
    confidence: float
    source: str
    provenance_present: bool


@dataclass(frozen=True)
class EvidenceFrame:
    """A deterministic, auditable view of observations for one farm snapshot."""

    farm_id: str
    crop_cycle_id: str
    as_of: datetime
    assessments: tuple[ObservationAlignment, ...]
    eligible_observation_ids: tuple[str, ...]
    excluded_counts: Mapping[str, int]


def align_observations(
    snapshot: FarmSnapshot,
    observations: Sequence[Observation],
    *,
    max_age_by_kind: Mapping[str, timedelta],
    require_provenance: bool = False,
) -> EvidenceFrame:
    """Align observations to a snapshot without inventing confidence values.

    Freshness windows are deployment/domain configuration. Missing windows are
    reported as freshness_unconfigured and the observation is not eligible.
    Crop-cycle-specific observations from another cycle, future observations,
    duplicate IDs, and stale observations are excluded. Farm-scoped observations
    with no crop_cycle_id remain eligible if their other checks pass.

    Quality and confidence are carried through separately; neither is interpreted
    as a calibrated probability. Missing provenance is always reported and only
    excludes an observation when require_provenance is true.
    """
    if snapshot.as_of.tzinfo is None or snapshot.as_of.utcoffset() is None:
        raise ValueError("snapshot.as_of must be timezone-aware")

    for kind, max_age in max_age_by_kind.items():
        if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
            raise ValueError(f"freshness window for {kind!r} must be positive")

    as_of = snapshot.as_of.astimezone(timezone.utc)
    counts = Counter(item.id for item in observations)
    duplicate_ids = {item_id for item_id, count in counts.items() if count > 1}
    assessments: list[ObservationAlignment] = []

    ordered = sorted(observations, key=lambda item: (item.timestamp, item.id), reverse=True)
    for item in ordered:
        timestamp = item.timestamp.astimezone(timezone.utc)
        age = (as_of - timestamp).total_seconds()
        issues: list[str] = []
        window = max_age_by_kind.get(item.kind)

        if item.id in duplicate_ids:
            issues.append("duplicate_observation_id")
        if item.crop_cycle_id is not None and item.crop_cycle_id != snapshot.active_crop.id:
            issues.append("crop_cycle_mismatch")
        if age < 0:
            freshness = "future"
            issues.append("observation_from_future")
        elif window is None:
            freshness = "unconfigured"
            issues.append("freshness_window_unconfigured")
        elif age > window.total_seconds():
            freshness = "stale"
            issues.append("observation_stale")
        else:
            freshness = "fresh"

        provenance_present = bool(item.provenance)
        if not provenance_present:
            issues.append("provenance_missing")
        if require_provenance and not provenance_present:
            issues.append("provenance_required")

        excluded_issues = {
            "duplicate_observation_id",
            "crop_cycle_mismatch",
            "observation_from_future",
            "freshness_window_unconfigured",
            "observation_stale",
        }
        if require_provenance:
            excluded_issues.add("provenance_required")
        eligible = not any(issue in excluded_issues for issue in issues)

        assessments.append(
            ObservationAlignment(
                observation_id=item.id,
                freshness=freshness,
                age_seconds=age,
                eligible=eligible,
                issues=tuple(issues),
                quality=item.quality,
                confidence=item.confidence,
                source=item.source,
                provenance_present=provenance_present,
            )
        )

    eligible_ids = tuple(
        assessment.observation_id
        for assessment in assessments
        if assessment.eligible
    )
    excluded_counts = Counter(
        issue
        for assessment in assessments
        if not assessment.eligible
        for issue in assessment.issues
    )
    return EvidenceFrame(
        farm_id=snapshot.farm_id,
        crop_cycle_id=snapshot.active_crop.id,
        as_of=as_of,
        assessments=tuple(assessments),
        eligible_observation_ids=eligible_ids,
        excluded_counts=dict(sorted(excluded_counts.items())),
    )
