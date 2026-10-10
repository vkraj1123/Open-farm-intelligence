"""Explicit, deterministic comparability checks for already-aligned observations.

A disagreement here means only that a caller-defined numeric tolerance was
exceeded after declared comparability gates passed. It is not an agronomic
contradiction or proof that either source is wrong.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations
from typing import Literal, Sequence

from ofi.domain.models import Observation
from ofi.services.evidence_alignment import EvidenceFrame


ComparisonStatus = Literal["agree", "disagree", "not_comparable"]


@dataclass(frozen=True)
class MetricComparisonRule:
    """Caller-owned contract describing when two observations may be compared."""

    rule_id: str
    value_key: str
    observation_kinds: tuple[str, ...]
    unit: str
    spatial_scope: str
    max_time_delta: timedelta
    absolute_tolerance: float
    require_same_crop_cycle: bool = True
    require_distinct_sources: bool = True

    def __post_init__(self) -> None:
        for name, value in (
            ("rule_id", self.rule_id),
            ("value_key", self.value_key),
            ("unit", self.unit),
            ("spatial_scope", self.spatial_scope),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        if not self.observation_kinds:
            raise ValueError("observation_kinds must not be empty")
        if any(not isinstance(kind, str) or not kind.strip() for kind in self.observation_kinds):
            raise ValueError("observation_kinds must contain non-blank values")
        if len(set(self.observation_kinds)) != len(self.observation_kinds):
            raise ValueError("observation_kinds must be unique")
        if not isinstance(self.max_time_delta, timedelta) or self.max_time_delta <= timedelta(0):
            raise ValueError("max_time_delta must be positive")
        if not math.isfinite(self.absolute_tolerance) or self.absolute_tolerance < 0:
            raise ValueError("absolute_tolerance must be finite and non-negative")


@dataclass(frozen=True)
class ObservationPairComparison:
    rule_id: str
    value_key: str
    left_observation_id: str
    right_observation_id: str
    left_source: str
    right_source: str
    left_value: float | None
    right_value: float | None
    absolute_difference: float | None
    status: ComparisonStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceComparisonReport:
    farm_id: str
    crop_cycle_id: str
    as_of: datetime
    comparisons: tuple[ObservationPairComparison, ...]


def _numeric_value(observation: Observation, key: str) -> float | None:
    value = observation.value.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def compare_aligned_observations(
    observations: Sequence[Observation],
    frame: EvidenceFrame,
    rules: Sequence[MetricComparisonRule],
) -> EvidenceComparisonReport:
    """Compare pairs only after eligibility and explicit comparability gates.

    Rules must state the metric key, participating observation kinds, expected
    unit, expected non-empty spatial scope, time separation limit and absolute
    tolerance. When enabled, crop-cycle matching requires both IDs to be known
    and equal; distinct-source matching requires two different non-empty sources.

    Pairs excluded by the supplied EvidenceFrame are reported as
    not_comparable rather than silently compared.
    """
    rule_ids = [rule.rule_id for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("comparison rule IDs must be unique")

    eligible_ids = set(frame.eligible_observation_ids)
    ordered_observations = sorted(observations, key=lambda item: (item.id, item.source))
    comparisons: list[ObservationPairComparison] = []

    for rule in sorted(rules, key=lambda item: item.rule_id):
        candidates = [
            item for item in ordered_observations
            if item.kind in rule.observation_kinds
        ]
        for left, right in combinations(candidates, 2):
            # Duplicate identifiers are already excluded by alignment; avoiding
            # same-ID pairs also prevents an ambiguous output identity.
            if left.id == right.id:
                continue

            left_value = _numeric_value(left, rule.value_key)
            right_value = _numeric_value(right, rule.value_key)
            reasons: list[str] = []

            if left.id not in eligible_ids or right.id not in eligible_ids:
                reasons.append("observation_not_eligible")
            if left_value is None:
                reasons.append("left_value_missing_or_non_numeric")
            if right_value is None:
                reasons.append("right_value_missing_or_non_numeric")
            if left.unit != rule.unit or right.unit != rule.unit:
                reasons.append("unit_mismatch_or_missing")
            if left.spatial_scope != rule.spatial_scope or right.spatial_scope != rule.spatial_scope:
                reasons.append("spatial_scope_mismatch_or_missing")
            if abs(left.timestamp - right.timestamp) > rule.max_time_delta:
                reasons.append("time_separation_exceeds_policy")
            if rule.require_same_crop_cycle:
                if (
                    left.crop_cycle_id is None
                    or right.crop_cycle_id is None
                    or left.crop_cycle_id != right.crop_cycle_id
                ):
                    reasons.append("crop_cycle_scope_missing_or_mismatch")
            if rule.require_distinct_sources:
                if not left.source.strip() or not right.source.strip():
                    reasons.append("source_missing")
                elif left.source.strip() == right.source.strip():
                    reasons.append("same_source")

            difference: float | None = None
            status: ComparisonStatus
            if reasons:
                status = "not_comparable"
            else:
                # Values are known finite numbers after the gates above.
                raw_difference = abs(left_value - right_value)  # type: ignore[operator]
                if not math.isfinite(raw_difference):
                    status = "not_comparable"
                    reasons.append("absolute_difference_non_finite")
                else:
                    difference = raw_difference
                    status = "agree" if difference <= rule.absolute_tolerance else "disagree"
                    if status == "disagree":
                        reasons.append("difference_exceeds_absolute_tolerance")

            comparisons.append(
                ObservationPairComparison(
                    rule_id=rule.rule_id,
                    value_key=rule.value_key,
                    left_observation_id=left.id,
                    right_observation_id=right.id,
                    left_source=left.source,
                    right_source=right.source,
                    left_value=left_value,
                    right_value=right_value,
                    absolute_difference=difference,
                    status=status,
                    reasons=tuple(sorted(set(reasons))),
                )
            )

    return EvidenceComparisonReport(
        farm_id=frame.farm_id,
        crop_cycle_id=frame.crop_cycle_id,
        as_of=frame.as_of,
        comparisons=tuple(comparisons),
    )
