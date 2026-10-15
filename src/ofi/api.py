from datetime import datetime, timedelta, timezone
import hashlib
import math
import secrets
import os
from uuid import uuid4
from typing import Literal, get_args

from fastapi import Depends, FastAPI, Header, HTTPException, Query
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
from ofi.services.tenant_membership_repository import (
    InMemoryTenantMembershipRepository,
    InvitationConflictError,
    InvitationUnavailableError,
    LastTenantAdminError,
    MembershipRole,
    MembershipStatus,
    PostgresTenantMembershipRepository,
    TenantInvitation,
    TenantMembershipRepository,
)
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


def _build_tenant_membership_repository(
    connection_factory=_UNSET_CONNECTION_FACTORY,
) -> TenantMembershipRepository:
    """Use durable membership state whenever durable application storage is configured."""
    if connection_factory is _UNSET_CONNECTION_FACTORY:
        connection_factory = _build_postgres_connection_factory()
    if connection_factory is None:
        return InMemoryTenantMembershipRepository()
    return PostgresTenantMembershipRepository(connection_factory)


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
tenant_membership_repository = _build_tenant_membership_repository(postgres_connection_factory)


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


class TenantMembershipGrantRequest(BaseModel):
    """Create or reactivate a tenant membership; tenant_id comes only from the route."""

    model_config = {"extra": "forbid"}

    actor_id: str = Field(min_length=1, max_length=512)
    membership_role: Literal["tenant_admin", "member"] = "member"

    @field_validator("actor_id")
    @classmethod
    def actor_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 512:
            raise ValueError("actor_id must be non-empty and at most 512 characters")
        return normalized


class TenantMembershipUpdateRequest(BaseModel):
    """Change one existing membership's role and/or active status."""

    model_config = {"extra": "forbid"}

    membership_role: Literal["tenant_admin", "member"]
    status: Literal["active", "suspended", "revoked"]



class TenantInvitationCreateRequest(BaseModel):
    """Invite a trusted identity-provider subject as a regular tenant member."""
    model_config = {"extra": "forbid"}
    actor_id: str = Field(min_length=1, max_length=512)
    expires_in_hours: int = Field(default=168, ge=1, le=720)

    @field_validator("actor_id")
    @classmethod
    def actor_id_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 512:
            raise ValueError("actor_id must be non-empty and at most 512 characters")
        return value


class TenantInvitationRedeemRequest(BaseModel):
    """Single-use secret presented by its intended authenticated identity."""
    model_config = {"extra": "forbid"}
    invitation_token: str = Field(min_length=32, max_length=256)

    @field_validator("invitation_token")
    @classmethod
    def token_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("invitation_token must not be blank")
        return value


def _membership_payload(membership) -> dict:
    return {
        "tenant_id": membership.tenant_id,
        "actor_id": membership.actor_id,
        "membership_role": membership.membership_role,
        "status": membership.status,
        "created_at": membership.created_at.isoformat(),
        "updated_at": membership.updated_at.isoformat(),
        "created_by": membership.created_by,
        "updated_by": membership.updated_by,
    }



def _invitation_payload(invitation: TenantInvitation) -> dict:
    """Serialize metadata only; token hashes and raw secrets are not exposed."""
    return {
        "invitation_id": invitation.invitation_id,
        "tenant_id": invitation.tenant_id,
        "invited_actor_id": invitation.invited_actor_id,
        "status": invitation.status,
        "created_at": invitation.created_at.isoformat(),
        "expires_at": invitation.expires_at.isoformat(),
        "created_by": invitation.created_by,
        "redeemed_at": invitation.redeemed_at.isoformat() if invitation.redeemed_at else None,
        "redeemed_by": invitation.redeemed_by,
        "revoked_at": invitation.revoked_at.isoformat() if invitation.revoked_at else None,
        "revoked_by": invitation.revoked_by,
    }


def _membership_enforcement_enabled() -> bool:
    mode = os.getenv("OFI_TENANT_MEMBERSHIP_ENFORCEMENT", "required").strip().lower()
    if mode == "required":
        return True
    if mode == "disabled" and os.getenv("OFI_ENV", "").strip().lower() == "test":
        return False
    raise HTTPException(
        status_code=503,
        detail="tenant membership enforcement configuration is invalid",
    )


def _require_active_tenant_membership(actor: ActorIdentity) -> None:
    """Check current membership state, not only the tenant claim signed into a token."""
    if not actor.tenant_id:
        raise HTTPException(status_code=401, detail="a tenant-scoped bearer token is required")
    if not _membership_enforcement_enabled() or actor.role == "system":
        return
    try:
        tenant_membership_repository.get_active(actor.tenant_id, actor.actor_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=403,
            detail="active tenant membership is required",
        ) from exc
    except Exception as exc:
        # The authorization decision is unavailable; do not fall back to the signed claim.
        raise HTTPException(
            status_code=503,
            detail="tenant membership verification is unavailable",
        ) from exc


def _authenticated_tenant_actor(
    actor: ActorIdentity = Depends(require_authenticated_actor),
) -> ActorIdentity:
    """Verified token identity plus current membership for consent boundaries."""
    _require_active_tenant_membership(actor)
    return actor


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
    actor: ActorIdentity = Depends(_authenticated_tenant_actor),
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
    actor: ActorIdentity = Depends(_authenticated_tenant_actor),
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
    actor: ActorIdentity = Depends(_authenticated_tenant_actor),
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
        if os.getenv("OFI_ENV", "").strip().lower() == "test":
            return None
        raise HTTPException(
            status_code=503,
            detail="resource authentication cannot be disabled outside test mode",
        )
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
    _require_active_tenant_membership(actor)
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


def _require_tenant_membership_admin(
    actor: ActorIdentity | None,
    tenant_id: str,
) -> bool:
    """Authorize membership administration; returns True for the platform system role."""
    normalized_tenant_id = tenant_id.strip() if isinstance(tenant_id, str) else ""
    if not normalized_tenant_id or len(normalized_tenant_id) > 512:
        raise HTTPException(status_code=422, detail="invalid tenant ID")
    if actor is None:
        raise HTTPException(status_code=401, detail="authenticated tenant administrator required")
    if actor.role == "system":
        return True
    if actor.tenant_id != normalized_tenant_id:
        raise HTTPException(status_code=404, detail="tenant not found")
    try:
        membership = tenant_membership_repository.get_active(
            normalized_tenant_id, actor.actor_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=403, detail="tenant administrator role is required") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant membership verification is unavailable") from exc
    if membership.membership_role != "tenant_admin":
        raise HTTPException(status_code=403, detail="tenant administrator role is required")
    return False


@app.post("/tenants/{tenant_id}/memberships", status_code=201)
def grant_tenant_membership(
    tenant_id: str,
    request: TenantMembershipGrantRequest,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Grant/reactivate a member; only platform system can grant tenant-admin role."""
    platform_admin = _require_tenant_membership_admin(actor, tenant_id)
    if request.membership_role == "tenant_admin" and not platform_admin:
        raise HTTPException(status_code=403, detail="only platform system may grant tenant-admin role")
    if not platform_admin:
        try:
            existing = tenant_membership_repository.get(tenant_id.strip(), request.actor_id)
        except KeyError:
            existing = None
        except Exception as exc:
            raise HTTPException(status_code=503, detail="tenant membership lookup is unavailable") from exc
        if existing is not None and existing.membership_role == "tenant_admin":
            raise HTTPException(
                status_code=403,
                detail="only platform system may change tenant-administrator roles",
            )
    try:
        membership = tenant_membership_repository.grant(
            tenant_id.strip(), request.actor_id, request.membership_role,
            granted_by=actor.actor_id,
        )
    except LastTenantAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant membership update is unavailable") from exc
    return _membership_payload(membership)


@app.get("/tenants/{tenant_id}/memberships")
def list_tenant_memberships(
    tenant_id: str,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """List memberships inside a tenant, visible only to its admins or platform system."""
    _require_tenant_membership_admin(actor, tenant_id)
    try:
        items = tenant_membership_repository.list_for_tenant(tenant_id.strip())
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant membership lookup is unavailable") from exc
    return {"tenant_id": tenant_id.strip(), "memberships": [_membership_payload(item) for item in items]}


@app.patch("/tenants/{tenant_id}/memberships/{member_actor_id}")
def update_tenant_membership(
    tenant_id: str,
    member_actor_id: str,
    request: TenantMembershipUpdateRequest,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Suspend, revoke, reactivate or change a member role with an audited update."""
    platform_admin = _require_tenant_membership_admin(actor, tenant_id)
    if not platform_admin:
        if request.membership_role != "member":
            raise HTTPException(status_code=403, detail="only platform system may change tenant-admin roles")
        try:
            target = tenant_membership_repository.get(tenant_id.strip(), member_actor_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="tenant membership not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="tenant membership lookup is unavailable") from exc
        if target.membership_role == "tenant_admin":
            raise HTTPException(status_code=403, detail="only platform system may manage tenant administrators")
    try:
        membership = tenant_membership_repository.update(
            tenant_id.strip(), member_actor_id, request.membership_role, request.status,
            changed_by=actor.actor_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="tenant membership not found") from exc
    except LastTenantAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant membership update is unavailable") from exc
    return _membership_payload(membership)


@app.get("/tenants/{tenant_id}/membership-events")
def list_tenant_membership_events(
    tenant_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Read the append-only membership administration audit log for an authorized tenant."""
    _require_tenant_membership_admin(actor, tenant_id)
    try:
        events = tenant_membership_repository.list_events(tenant_id.strip(), limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant membership audit lookup is unavailable") from exc
    return {"tenant_id": tenant_id.strip(), "events": [
        {**event, "occurred_at": event["occurred_at"].isoformat()}
        for event in events
    ]}



@app.post("/tenants/{tenant_id}/invitations", status_code=201)
def create_tenant_invitation(
    tenant_id: str, request: TenantInvitationCreateRequest,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Issue a one-time invite for a stable, externally authenticated subject.

    The raw token is returned only in this response; delivery is managed externally.
    """
    _require_tenant_membership_admin(actor, tenant_id)
    if actor is None:
        raise HTTPException(status_code=401, detail="authenticated tenant administrator required")
    token = secrets.token_urlsafe(32)
    token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=request.expires_in_hours)
    try:
        invitation = tenant_membership_repository.create_invitation(
            str(uuid4()), tenant_id.strip(), request.actor_id, token_sha256,
            expires_at, created_by=actor.actor_id,
        )
    except InvitationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant invitation creation is unavailable") from exc
    # This response contains a one-time credential: explicitly prohibit cache storage.
    return JSONResponse(
        content={
            "invitation": _invitation_payload(invitation),
            "invitation_token": token,
            "delivery": "manual_or_external",
        },
        headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"},
    )


@app.get("/tenants/{tenant_id}/invitations")
def list_tenant_invitations(
    tenant_id: str, limit: int = Query(default=100, ge=1, le=500),
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """List invitation metadata without exposing raw secrets or token digests."""
    _require_tenant_membership_admin(actor, tenant_id)
    if actor is None:
        raise HTTPException(status_code=401, detail="authenticated tenant administrator required")
    try:
        items = tenant_membership_repository.list_invitations(
            tenant_id.strip(), checked_by=actor.actor_id, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant invitation lookup is unavailable") from exc
    return {"tenant_id": tenant_id.strip(), "invitations": [_invitation_payload(item) for item in items]}


@app.post("/tenants/{tenant_id}/invitations/redeem")
def redeem_tenant_invitation(
    tenant_id: str, request: TenantInvitationRedeemRequest,
    actor: ActorIdentity = Depends(require_authenticated_actor),
):
    """Redeem an invitation only as its intended authenticated subject."""
    if actor.role == "system":
        raise HTTPException(status_code=403, detail="platform system identities cannot redeem tenant invitations")
    token_sha256 = hashlib.sha256(request.invitation_token.encode("utf-8")).hexdigest()
    try:
        membership = tenant_membership_repository.redeem_invitation(
            token_sha256, actor.actor_id, tenant_id=tenant_id.strip()
        )
    except InvitationUnavailableError as exc:
        raise HTTPException(status_code=400, detail="invitation is invalid or unavailable") from exc
    except InvitationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant invitation redemption is unavailable") from exc
    return _membership_payload(membership)


@app.post("/tenants/{tenant_id}/invitations/{invitation_id}/revoke")
def revoke_tenant_invitation(
    tenant_id: str, invitation_id: str,
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Revoke a pending invitation and append a lifecycle audit event."""
    _require_tenant_membership_admin(actor, tenant_id)
    if actor is None:
        raise HTTPException(status_code=401, detail="authenticated tenant administrator required")
    try:
        invitation = tenant_membership_repository.revoke_invitation(
            tenant_id.strip(), invitation_id, revoked_by=actor.actor_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="invitation not found") from exc
    except InvitationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant invitation update is unavailable") from exc
    return _invitation_payload(invitation)


@app.get("/tenants/{tenant_id}/invitation-events")
def list_tenant_invitation_events(
    tenant_id: str, limit: int = Query(default=100, ge=1, le=500),
    actor: ActorIdentity | None = Depends(_resource_actor),
):
    """Read tenant-scoped invitation audit history."""
    _require_tenant_membership_admin(actor, tenant_id)
    try:
        events = tenant_membership_repository.list_invitation_events(tenant_id.strip(), limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="tenant invitation audit lookup is unavailable") from exc
    return {"tenant_id": tenant_id.strip(), "events": [
        {**event, "occurred_at": event["occurred_at"].isoformat()} for event in events
    ]}



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
        return case_manager.record_outcome(
            case_id,
            outcome,
            tenant_id=actor.tenant_id if actor is not None else None,
        )
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
