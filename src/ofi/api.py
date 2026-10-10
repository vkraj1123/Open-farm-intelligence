from datetime import datetime, timedelta
import math
from typing import get_args

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from ofi.domain.models import CaseOutcome, CaseRecord, FarmCase, Observation, ObservationKind
from ofi.providers.registry import default_mock_registry
from ofi.services.case_manager import CaseManager
from ofi.services.evidence_alignment import align_observations
from ofi.services.evidence_comparison import MetricComparisonRule, compare_aligned_observations


app = FastAPI(
    title="Open Farm Intelligence",
    version="0.2.0",
    description="Farm-level intelligence and orchestration layer for Indian agriculture.",
)
case_manager = CaseManager()
provider_registry = default_mock_registry()


class EvidenceFrameRequest(BaseModel):
    """Caller-owned freshness policy for a one-time evidence-frame request."""

    max_age_seconds_by_kind: dict[str, float] = Field(min_length=1)
    require_provenance: bool = False

    @field_validator("max_age_seconds_by_kind")
    @classmethod
    def freshness_windows_must_be_finite_and_positive(
        cls, value: dict[str, float]
    ) -> dict[str, float]:
        for kind, seconds in value.items():
            if not kind.strip():
                raise ValueError("observation kind must not be blank")
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("freshness windows must be finite positive seconds")
            try:
                timedelta(seconds=seconds)
            except OverflowError as exc:
                raise ValueError("freshness window is too large") from exc
        return value


class MetricComparisonRuleRequest(BaseModel):
    """Caller-declared contract for comparing one numeric observation field."""

    rule_id: str = Field(min_length=1)
    value_key: str = Field(min_length=1)
    observation_kinds: list[str] = Field(min_length=1)
    unit: str = Field(min_length=1)
    spatial_scope: str = Field(min_length=1)
    max_time_delta_seconds: float = Field(gt=0, allow_inf_nan=False)
    absolute_tolerance: float = Field(ge=0, allow_inf_nan=False)
    require_same_crop_cycle: bool = True
    require_distinct_sources: bool = True

    @field_validator("rule_id", "value_key", "unit", "spatial_scope")
    @classmethod
    def rule_strings_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rule strings must not be blank")
        return value

    @field_validator("max_time_delta_seconds")
    @classmethod
    def time_delta_must_fit_datetime_precision(cls, value: float) -> float:
        try:
            timedelta(seconds=value)
        except OverflowError as exc:
            raise ValueError("max_time_delta_seconds is too large") from exc
        return value

    @field_validator("observation_kinds")
    @classmethod
    def kinds_must_be_supported_and_unique(cls, value: list[str]) -> list[str]:
        supported = set(get_args(ObservationKind))
        if any(kind not in supported for kind in value):
            raise ValueError("observation_kinds contains an unsupported observation kind")
        if len(value) != len(set(value)):
            raise ValueError("observation_kinds must be unique")
        return value


class EvidenceComparisonRequest(EvidenceFrameRequest):
    """Freshness policy plus explicit pairwise metric-comparison rules."""

    comparison_rules: list[MetricComparisonRuleRequest] = Field(min_length=1)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/cases", response_model=CaseRecord)
def create_case(case: FarmCase):
    try:
        return case_manager.create(case)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/cases/{case_id}", response_model=CaseRecord)
def get_case(case_id: str):
    try:
        return case_manager.store.get(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.post("/cases/{case_id}/observations", response_model=CaseRecord)
def add_observation(case_id: str, observation: Observation):
    try:
        return case_manager.add_observation(case_id, observation, actor="api")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.post("/cases/{case_id}/reason", response_model=CaseRecord)
def reason(case_id: str):
    try:
        return case_manager.reason(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.post("/cases/{case_id}/outcome", response_model=CaseRecord)
def record_outcome(case_id: str, outcome: CaseOutcome):
    try:
        return case_manager.record_outcome(case_id, outcome)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.post("/cases/{case_id}/escalate", response_model=CaseRecord)
def escalate(case_id: str):
    try:
        return case_manager.escalate(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.post("/farms/{farm_id}/collect-evidence")
def collect_evidence(farm_id: str):
    try:
        snapshot = case_manager.farm_twin.snapshot(farm_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="farm snapshot not available") from exc
    observations = provider_registry.collect(snapshot)
    for observation in observations:
        case_manager.farm_twin.add_observation(farm_id, observation)
    return {"farm_id": farm_id, "observations": observations}


@app.post("/farms/{farm_id}/evidence-frame")
def get_evidence_frame(farm_id: str, request: EvidenceFrameRequest):
    """Return a deterministic evidence eligibility report for the current farm snapshot."""
    try:
        snapshot = case_manager.farm_twin.snapshot(farm_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="farm snapshot not available") from exc

    frame = align_observations(
        snapshot,
        snapshot.recent_observations,
        max_age_by_kind={
            kind: timedelta(seconds=seconds)
            for kind, seconds in request.max_age_seconds_by_kind.items()
        },
        require_provenance=request.require_provenance,
    )
    return {
        "farm_id": frame.farm_id,
        "crop_cycle_id": frame.crop_cycle_id,
        "as_of": frame.as_of,
        "eligible_observation_ids": list(frame.eligible_observation_ids),
        "excluded_counts": dict(frame.excluded_counts),
        "assessments": [
            {
                "observation_id": item.observation_id,
                "freshness": item.freshness,
                "age_seconds": item.age_seconds,
                "eligible": item.eligible,
                "issues": list(item.issues),
                "quality": item.quality,
                "confidence": item.confidence,
                "source": item.source,
                "provenance_present": item.provenance_present,
            }
            for item in frame.assessments
        ],
    }

@app.post("/cases/{case_id}/reason-with-evidence-policy")
def reason_with_evidence_policy(case_id: str, request: EvidenceFrameRequest):
    """Reason over eligible evidence only, using an explicit caller-owned policy."""
    try:
        record, frame = case_manager.reason_with_evidence_policy(
            case_id,
            max_age_by_kind={
                kind: timedelta(seconds=seconds)
                for kind, seconds in request.max_age_seconds_by_kind.items()
            },
            require_provenance=request.require_provenance,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "record": record,
        "evidence_frame": {
            "farm_id": frame.farm_id,
            "crop_cycle_id": frame.crop_cycle_id,
            "as_of": frame.as_of,
            "eligible_observation_ids": list(frame.eligible_observation_ids),
            "excluded_counts": dict(frame.excluded_counts),
            "assessments": [
                {
                    "observation_id": item.observation_id,
                    "freshness": item.freshness,
                    "age_seconds": item.age_seconds,
                    "eligible": item.eligible,
                    "issues": list(item.issues),
                    "quality": item.quality,
                    "confidence": item.confidence,
                    "source": item.source,
                    "provenance_present": item.provenance_present,
                }
                for item in frame.assessments
            ],
        },
    }


@app.post("/farms/{farm_id}/evidence-comparisons")
def compare_farm_evidence(farm_id: str, request: EvidenceComparisonRequest):
    """Assess pairwise comparability using explicit caller-owned rules."""
    try:
        snapshot = case_manager.farm_twin.snapshot(farm_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="farm snapshot not available") from exc

    try:
        frame = align_observations(
            snapshot,
            snapshot.recent_observations,
            max_age_by_kind={
                kind: timedelta(seconds=seconds)
                for kind, seconds in request.max_age_seconds_by_kind.items()
            },
            require_provenance=request.require_provenance,
        )
        rules = [
            MetricComparisonRule(
                rule_id=item.rule_id,
                value_key=item.value_key,
                observation_kinds=tuple(item.observation_kinds),
                unit=item.unit,
                spatial_scope=item.spatial_scope,
                max_time_delta=timedelta(seconds=item.max_time_delta_seconds),
                absolute_tolerance=item.absolute_tolerance,
                require_same_crop_cycle=item.require_same_crop_cycle,
                require_distinct_sources=item.require_distinct_sources,
            )
            for item in request.comparison_rules
        ]
        report = compare_aligned_observations(snapshot.recent_observations, frame, rules)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "farm_id": report.farm_id,
        "crop_cycle_id": report.crop_cycle_id,
        "as_of": report.as_of,
        "eligible_observation_ids": list(frame.eligible_observation_ids),
        "excluded_counts": dict(frame.excluded_counts),
        "assessments": [
            {
                "observation_id": item.observation_id,
                "freshness": item.freshness,
                "age_seconds": item.age_seconds,
                "eligible": item.eligible,
                "issues": list(item.issues),
                "quality": item.quality,
                "confidence": item.confidence,
                "source": item.source,
                "provenance_present": item.provenance_present,
            }
            for item in frame.assessments
        ],
        "comparisons": [
            {
                "rule_id": item.rule_id,
                "value_key": item.value_key,
                "left_observation_id": item.left_observation_id,
                "right_observation_id": item.right_observation_id,
                "left_source": item.left_source,
                "right_source": item.right_source,
                "left_value": item.left_value,
                "right_value": item.right_value,
                "absolute_difference": item.absolute_difference,
                "status": item.status,
                "reasons": list(item.reasons),
            }
            for item in report.comparisons
        ],
    }
