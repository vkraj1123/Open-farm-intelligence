from datetime import datetime, timedelta, timezone
import math
import os
from uuid import uuid4
from typing import Literal, get_args

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from ofi.domain.models import CaseOutcome, CaseRecord, FarmCase, Observation, ObservationKind
from ofi.providers.registry import default_mock_registry
from ofi.services.case_manager import CaseManager

from ofi.services.consent_repository import (
    ConsentRepository,
    InMemoryConsentRepository,
    PostgresConsentRepository,
)
from ofi.services.execution_gateway import ActorIdentity, ConsentGrant
from ofi.api_auth import require_authenticated_actor
from ofi.services.database_readiness import check_persistence_readiness
from ofi.services.evidence_alignment import align_observations
from ofi.services.unit_of_work import PostgresFarmCaseUnitOfWork
from ofi.services.evidence_comparison import MetricComparisonRule, compare_aligned_observations
from ofi.services.evidence_comparison_ledger import (
    EvidenceComparisonAssessment,
    EvidenceComparisonLedger,
    InMemoryEvidenceComparisonLedger,
    PostgresEvidenceComparisonLedger,
)


def _build_postgres_connection_factory():
    """Return a shared-configuration connection factory, or None for memory mode.

    A configured DSN is fail-closed: missing PostgreSQL dependencies must not
    silently switch the application back to process-local state.
    """
    dsn = os.getenv("OFI_DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "OFI_DATABASE_URL is configured but psycopg is not installed; "
            "install the postgres extra to enable durable case/farm persistence"
        ) from exc
    return lambda: psycopg.connect(dsn)


_UNSET_CONNECTION_FACTORY = object()


def _build_case_manager(connection_factory=_UNSET_CONNECTION_FACTORY) -> CaseManager:
    """Select coordinated durable storage, or the in-memory reference stores."""
    if connection_factory is _UNSET_CONNECTION_FACTORY:
        connection_factory = _build_postgres_connection_factory()
    if connection_factory is None:
        return CaseManager()
    return CaseManager(unit_of_work=PostgresFarmCaseUnitOfWork(connection_factory))


def _build_consent_repository(connection_factory=_UNSET_CONNECTION_FACTORY) -> ConsentRepository:
    """Use the shared durable database connection, or process-local memory."""
    if connection_factory is _UNSET_CONNECTION_FACTORY:
        connection_factory = _build_postgres_connection_factory()
    if connection_factory is None:
        return InMemoryConsentRepository()
    return PostgresConsentRepository(connection_factory)


app = FastAPI(
    title="Open Farm Intelligence",
    version="0.2.0",
    description="Farm-level intelligence and orchestration layer for Indian agriculture.",
)
# Resolve persistence configuration once so readiness and every repository
# describe the same application configuration.
postgres_connection_factory = _build_postgres_connection_factory()
case_manager = _build_case_manager(postgres_connection_factory)
provider_registry = default_mock_registry()

consent_repository = _build_consent_repository(postgres_connection_factory)


class ConsentGrantRequest(BaseModel):
    """Explicit affirmative consent to named services, from the verified actor."""

    model_config = {"extra": "forbid"}

    purpose: str = Field(min_length=1, max_length=500)
    scopes: list[str] = Field(min_length=1, max_length=25)
    affirmative: Literal[True]
    expires_at: datetime | None = None

    @field_validator("purpose")
    @classmethod
    def purpose_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("purpose must not be blank")
        return normalized

    @field_validator("scopes")
    @classmethod
    def scopes_must_be_unique_nonempty_values(cls, value: list[str]) -> list[str]:
        normalized = [scope.strip() if isinstance(scope, str) else "" for scope in value]
        if any(not scope or len(scope) > 120 for scope in normalized):
            raise ValueError("each service scope must be non-empty and at most 120 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("service scopes must be unique")
        return normalized

    @field_validator("expires_at")
    @classmethod
    def expiry_must_have_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expires_at must be timezone-aware")
        return value


def _require_consent_subject(actor: ActorIdentity) -> None:
    if not actor.tenant_id:
        raise HTTPException(
            status_code=401,
            detail="a tenant-scoped bearer token is required",
        )
    if actor.role not in {"farmer", "cultivator", "owner"}:
        raise HTTPException(
            status_code=403,
            detail="only a farmer, cultivator, or owner may manage consent",
        )


def _consent_payload(grant: ConsentGrant) -> dict:
    return {
        "consent_id": grant.consent_id,
        "tenant_id": grant.tenant_id,
        "actor_id": grant.actor_id,
        "purpose": grant.purpose,
        "status": grant.status,
        "scopes": list(grant.scopes),
        "granted_at": grant.granted_at.isoformat(),
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
        "active": grant.active(),
    }


@app.post("/consents", status_code=201)
def create_consent(
    request: ConsentGrantRequest,
    actor: ActorIdentity = Depends(require_authenticated_actor),
):
    """Create a scoped grant for the verified actor after affirmative consent."""
    _require_consent_subject(actor)
    now = datetime.now(timezone.utc)
    try:
        grant = ConsentGrant(
            actor_id=actor.actor_id,
            tenant_id=actor.tenant_id,
            purpose=request.purpose,
            status="granted",
            granted_at=now,
            expires_at=request.expires_at,
            scopes=tuple(request.scopes),
        )
        stored = consent_repository.create(grant)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _consent_payload(stored)


@app.get("/consents/{consent_id}")
def get_consent(
    consent_id: str,
    actor: ActorIdentity = Depends(require_authenticated_actor),
):
    """Return only a grant owned by the verified actor."""
    _require_consent_subject(actor)
    try:
        grant = consent_repository.get(consent_id, tenant_id=actor.tenant_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="consent grant not found") from exc
    if grant.actor_id != actor.actor_id or grant.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=404, detail="consent grant not found")
    return _consent_payload(grant)


@app.delete("/consents/{consent_id}")
def revoke_consent(
    consent_id: str,
    actor: ActorIdentity = Depends(require_authenticated_actor),
):
    """Record actor-bound revocation without deleting the consent record."""
    _require_consent_subject(actor)
    try:
        grant = consent_repository.revoke(
            consent_id,
            actor_id=actor.actor_id,
            tenant_id=actor.tenant_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="consent grant not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _consent_payload(grant)


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

class EvidenceComparisonPolicyRequest(EvidenceComparisonRequest):
    """Immutable policy definition; submitting the same policy_id makes a new version."""

    policy_id: str = Field(min_length=1)
    description: str = ""

    @field_validator("policy_id")
    @classmethod
    def policy_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy_id must not be blank")
        return value


class EvidenceComparisonAssessmentRequest(BaseModel):
    """Select an immutable comparison policy version for one farm assessment."""

    policy_id: str = Field(min_length=1)
    policy_version: int | None = Field(default=None, ge=1)

    @field_validator("policy_id")
    @classmethod
    def selected_policy_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy_id must not be blank")
        return value


def _build_evidence_comparison_ledger(
    connection_factory=_UNSET_CONNECTION_FACTORY,
) -> EvidenceComparisonLedger:
    """Use the same configured connection factory as case/farm persistence."""
    if connection_factory is _UNSET_CONNECTION_FACTORY:
        connection_factory = _build_postgres_connection_factory()
    if connection_factory is None:
        return InMemoryEvidenceComparisonLedger()
    return PostgresEvidenceComparisonLedger(connection_factory)


def _policy_payload(policy):
    return {
        "policy_id": policy.policy_id,
        "version": policy.version,
        "description": policy.description,
        "max_age_seconds_by_kind": policy.max_age_seconds_by_kind,
        "require_provenance": policy.require_provenance,
        "comparison_rules": [
            {
                "rule_id": rule.rule_id,
                "value_key": rule.value_key,
                "observation_kinds": list(rule.observation_kinds),
                "unit": rule.unit,
                "spatial_scope": rule.spatial_scope,
                "max_time_delta_seconds": rule.max_time_delta.total_seconds(),
                "absolute_tolerance": rule.absolute_tolerance,
                "require_same_crop_cycle": rule.require_same_crop_cycle,
                "require_distinct_sources": rule.require_distinct_sources,
            }
            for rule in policy.comparison_rules
        ],
        "created_at": policy.created_at,
    }


def _assessment_payload(assessment: EvidenceComparisonAssessment):
    return {
        "assessment_id": assessment.assessment_id,
        "policy_id": assessment.policy_id,
        "policy_version": assessment.policy_version,
        "farm_id": assessment.farm_id,
        "crop_cycle_id": assessment.crop_cycle_id,
        "as_of": assessment.as_of,
        "recorded_at": assessment.recorded_at,
        "observations_snapshot": assessment.observations_snapshot,
        "evidence_frame": assessment.evidence_frame,
        "comparisons": assessment.comparisons,
    }


def _evidence_frame_payload(frame):
    return {
        "farm_id": frame.farm_id,
        "crop_cycle_id": frame.crop_cycle_id,
        "as_of": frame.as_of.isoformat(),
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


def _comparison_payload(report):
    return [
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
    ]


evidence_comparison_ledger = _build_evidence_comparison_ledger(
    postgres_connection_factory
)


@app.get("/health")
def health():
    """Liveness-only endpoint; it deliberately does not require database access."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Report whether configured persistence can serve application requests."""
    report = check_persistence_readiness(postgres_connection_factory)
    status_code = 200 if report["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=report)


def _resource_actor(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ActorIdentity | None:
    """Require verified identity by default; the opt-out is for isolated tests only."""
    mode = os.getenv("OFI_RESOURCE_AUTH_ENABLED", "true").strip().lower()
    if mode in {"0", "false", "no", "off"}:
        return None
    if mode not in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=503,
            detail="resource authentication configuration is invalid",
        )
    actor = require_authenticated_actor(authorization)
    if not actor.tenant_id:
        raise HTTPException(
            status_code=401,
            detail="a tenant-scoped bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return actor


def _current_tenant_id(actor: ActorIdentity | None) -> str:
    if actor is None:
        return "legacy:default"
    if not actor.tenant_id:
        raise HTTPException(status_code=401, detail="a tenant-scoped bearer token is required")
    return actor.tenant_id


def _actor_can_access_farm(actor: ActorIdentity, farm) -> bool:
    """Allow tenant members with a primary-farmer or verified-party relationship."""
    if (
        not actor.authenticated
        or not actor.tenant_id
        or (farm.tenant_id or "legacy:default") != actor.tenant_id
        or actor.role not in {"farmer", "owner", "cultivator"}
    ):
        return False
    if actor.role == "farmer" and actor.actor_id == farm.farmer_id:
        return True

    today = datetime.now(timezone.utc).date()
    return any(
        party.party_id == actor.actor_id
        and party.role == actor.role
        and party.verification == "verified"
        and party.valid_from <= today
        and (party.valid_to is None or party.valid_to >= today)
        for party in farm.parties
    )


def _require_farm_access(actor: ActorIdentity | None, farm) -> None:
    if actor is not None and not _actor_can_access_farm(actor, farm):
        # Keep resource existence private across actor boundaries.
        raise HTTPException(status_code=404, detail="farm not found")


def _farm_or_404(farm_id: str, actor: ActorIdentity | None):
    try:
        farm = case_manager.farm_twin.get(
            farm_id,
            tenant_id=actor.tenant_id if actor is not None else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="farm not found") from exc
    _require_farm_access(actor, farm)
    return farm


def _case_or_404(case_id: str, actor: ActorIdentity | None):
    try:
        record = case_manager.store.get(
            case_id,
            tenant_id=actor.tenant_id if actor is not None else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc

    if actor is not None:
        try:
            farm = case_manager.farm_twin.get(
                record.case.farm.id,
                tenant_id=actor.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="case not found") from exc
        _require_farm_access(actor, farm)
        if record.case.farm.tenant_id != farm.tenant_id:
            raise HTTPException(status_code=404, detail="case not found")
    return record


def _require_policy_manager(actor: ActorIdentity | None) -> None:
    if actor is not None and actor.role not in {"agent", "service_provider", "system"}:
        raise HTTPException(
            status_code=403,
            detail="this role cannot manage global evidence-comparison policies",
        )


@app.post("/cases", response_model=CaseRecord)
def create_case(
    case: FarmCase,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    tenant_id = _current_tenant_id(actor)
    if case.farm.tenant_id is not None and case.farm.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="farm not found")
    farm_data = case.farm.model_copy(update={"tenant_id": tenant_id})
    case = case.model_copy(update={"farm": farm_data})
    if actor is not None:
        _require_farm_access(actor, case.farm)
        # Case creation must not act as an implicit farm transfer or profile update.
        try:
            # This guarded collision check intentionally sees whether the ID
            # already exists, so a cross-tenant collision can return the same
            # 404 as an inaccessible resource instead of leaking a 409.
            existing_farm = case_manager.farm_twin.get(case.farm.id)
        except KeyError:
            existing_farm = None
        if existing_farm is not None:
            _require_farm_access(actor, existing_farm)
            if case.farm.farmer_id != existing_farm.farmer_id:
                raise HTTPException(status_code=409, detail="farm owner cannot be changed through case creation")
            case = case.model_copy(update={"farm": existing_farm})
    try:
        return case_manager.create(case)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/cases/{case_id}", response_model=CaseRecord)
def get_case(
    case_id: str,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    return _case_or_404(case_id, actor)


@app.post("/cases/{case_id}/observations", response_model=CaseRecord)
def add_observation(
    case_id: str,
    observation: Observation,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    _case_or_404(case_id, actor)
    try:
        return case_manager.add_observation(
            case_id,
            observation,
            actor=actor.actor_id if actor else "api",
            tenant_id=actor.tenant_id if actor is not None else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.post("/cases/{case_id}/reason", response_model=CaseRecord)
def reason(
    case_id: str,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    _case_or_404(case_id, actor)
    try:
        return case_manager.reason(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.post("/cases/{case_id}/outcome", response_model=CaseRecord)
def record_outcome(
    case_id: str,
    outcome: CaseOutcome,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    _case_or_404(case_id, actor)
    try:
        return case_manager.record_outcome(case_id, outcome)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.post("/cases/{case_id}/escalate", response_model=CaseRecord)
def escalate(
    case_id: str,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    _case_or_404(case_id, actor)
    try:
        return case_manager.escalate(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc


@app.post("/farms/{farm_id}/collect-evidence")
def collect_evidence(
    farm_id: str,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    _farm_or_404(farm_id, actor)
    try:
        snapshot = case_manager.farm_twin.snapshot(
            farm_id,
            tenant_id=actor.tenant_id if actor is not None else None,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="farm snapshot not available") from exc
    observations = provider_registry.collect(snapshot)
    for observation in observations:
        case_manager.farm_twin.add_observation(
            farm_id,
            observation,
            tenant_id=actor.tenant_id if actor is not None else None,
        )
    return {"farm_id": farm_id, "observations": observations}


@app.post("/farms/{farm_id}/evidence-frame")
def get_evidence_frame(
    farm_id: str,
    request: EvidenceFrameRequest,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Return a deterministic evidence eligibility report for the current farm snapshot."""
    _farm_or_404(farm_id, actor)
    try:
        snapshot = case_manager.farm_twin.snapshot(
            farm_id,
            tenant_id=actor.tenant_id if actor is not None else None,
        )
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
def reason_with_evidence_policy(
    case_id: str,
    request: EvidenceFrameRequest,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Reason over eligible evidence only, using an explicit caller-owned policy."""
    _case_or_404(case_id, actor)
    try:
        record, frame = case_manager.reason_with_evidence_policy(
            case_id,
            max_age_by_kind={
                kind: timedelta(seconds=seconds)
                for kind, seconds in request.max_age_seconds_by_kind.items()
            },
            require_provenance=request.require_provenance,
            tenant_id=actor.tenant_id if actor is not None else None,
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
def compare_farm_evidence(
    farm_id: str,
    request: EvidenceComparisonRequest,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Assess pairwise comparability using explicit caller-owned rules."""
    _farm_or_404(farm_id, actor)
    try:
        snapshot = case_manager.farm_twin.snapshot(
            farm_id,
            tenant_id=actor.tenant_id if actor is not None else None,
        )
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


@app.post("/evidence-comparison-policies")
def create_evidence_comparison_policy(
    request: EvidenceComparisonPolicyRequest,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Create an immutable policy version; same policy_id publishes the next version."""
    _require_policy_manager(actor)
    try:
        policy = evidence_comparison_ledger.create_policy_version(
            request.policy_id,
            description=request.description,
            max_age_seconds_by_kind=request.max_age_seconds_by_kind,
            require_provenance=request.require_provenance,
            tenant_id=_current_tenant_id(actor),
            comparison_rules=[
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
            ],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _policy_payload(policy)


@app.get("/evidence-comparison-policies/{policy_id}")
def get_evidence_comparison_policy(
    policy_id: str,
    version: int | None = None,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Get a specific immutable policy version, or the current latest version."""
    if version is not None and version < 1:
        raise HTTPException(status_code=422, detail="version must be positive")
    try:
        return _policy_payload(
            evidence_comparison_ledger.get_policy(
                policy_id, version, tenant_id=_current_tenant_id(actor)
            )
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="comparison policy version not found") from exc


@app.post("/farms/{farm_id}/evidence-comparison-assessments")
def create_evidence_comparison_assessment(
    farm_id: str,
    request: EvidenceComparisonAssessmentRequest,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Run and persist an assessment with its exact policy and observation snapshot."""
    _farm_or_404(farm_id, actor)
    try:
        tenant_id = _current_tenant_id(actor)
        policy = evidence_comparison_ledger.get_policy(
            request.policy_id, request.policy_version, tenant_id=tenant_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="comparison policy version not found") from exc

    try:
        snapshot = case_manager.farm_twin.snapshot(
            farm_id,
            tenant_id=actor.tenant_id if actor is not None else None,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="farm snapshot not available") from exc

    frame = align_observations(
        snapshot,
        snapshot.recent_observations,
        max_age_by_kind={
            kind: timedelta(seconds=seconds)
            for kind, seconds in policy.max_age_seconds_by_kind.items()
        },
        require_provenance=policy.require_provenance,
    )
    report = compare_aligned_observations(
        snapshot.recent_observations, frame, policy.comparison_rules
    )
    assessment = EvidenceComparisonAssessment(
        assessment_id=f"evidence-assessment-{uuid4().hex}",
        tenant_id=tenant_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        farm_id=report.farm_id,
        crop_cycle_id=report.crop_cycle_id,
        as_of=report.as_of,
        recorded_at=datetime.now(timezone.utc),
        observations_snapshot=[
            item.model_dump(mode="json") for item in snapshot.recent_observations
        ],
        evidence_frame=_evidence_frame_payload(frame),
        comparisons=_comparison_payload(report),
    )
    try:
        stored = evidence_comparison_ledger.save_assessment(assessment)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _assessment_payload(stored)


@app.get("/evidence-comparison-assessments/{assessment_id}")
def get_evidence_comparison_assessment(
    assessment_id: str,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Retrieve an immutable historical comparison assessment."""
    try:
        assessment = evidence_comparison_ledger.get_assessment(
            assessment_id, tenant_id=_current_tenant_id(actor)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="evidence comparison assessment not found") from exc
    _farm_or_404(assessment.farm_id, actor)
    return _assessment_payload(assessment)
