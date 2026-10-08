from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, Field, field_validator


class GeoPoint(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Parcel(BaseModel):
    id: str
    location: GeoPoint
    area_ha: float | None = Field(default=None, gt=0)
    boundary: list[GeoPoint] = Field(default_factory=list)
    administrative_area: dict[str, str] = Field(default_factory=dict)


class LandParty(BaseModel):
    party_id: str
    role: Literal["owner", "cultivator", "manager", "lessor"]
    valid_from: date
    valid_to: date | None = None
    verification: Literal["declared", "corroborated", "verified"] = "declared"


class ProductionContract(BaseModel):
    """Optional seasonal production relationship; it does not imply possession."""
    contract_id: str
    owner_id: str
    cultivator_id: str
    valid_from: date
    valid_to: date
    arrangement: str | None = None


class CropCycle(BaseModel):
    id: str = Field(default_factory=lambda: f"crop-{uuid4().hex}")
    crop: str
    season: str | None = None
    sowing_date: date | None = None
    harvest_date: date | None = None
    irrigation_method: str | None = None
    status: Literal["planned", "active", "harvested", "failed"] = "active"


class Farm(BaseModel):
    id: str
    farmer_id: str
    parcel: Parcel
    crop_cycle: CropCycle
    parties: list[LandParty] = Field(default_factory=list)
    contracts: list[ProductionContract] = Field(default_factory=list)


ObservationKind = Literal[
    "farmer_report", "image", "soil", "weather", "satellite",
    "market", "sensor", "model", "expert"
]


class Observation(BaseModel):
    id: str
    kind: ObservationKind
    timestamp: datetime
    value: dict[str, Any]
    source: str
    quality: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    location: GeoPoint | None = None
    crop_cycle_id: str | None = None
    unit: str | None = None
    spatial_scope: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)


class CaseStatus:
    REPORTED = "reported"
    TRIAGED = "triaged"
    ACTIONED = "actioned"
    OBSERVING = "observing"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class CaseEvent(BaseModel):
    id: str
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)


class FarmCase(BaseModel):
    id: str
    farm: Farm
    query: str
    observations: list[Observation] = Field(default_factory=list)
    status: Literal["reported", "triaged", "actioned", "observing", "resolved", "escalated"] = "reported"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[CaseEvent] = Field(default_factory=list)

    @field_validator("created_at", "updated_at")
    @classmethod
    def case_timestamps_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("case timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class Evidence(BaseModel):
    id: str
    proposition: str
    observation_ids: list[str] = Field(default_factory=list)
    score: float = Field(ge=0.0, le=1.0)
    freshness: float = Field(ge=0.0, le=1.0)
    quality: float = Field(ge=0.0, le=1.0)


class Hypothesis(BaseModel):
    code: str
    label: str
    score: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)


Action = Literal["ADVISE", "ASK_FARMER", "REQUEST_TEST", "ESCALATE_EXPERT", "ROUTE_SERVICE"]


class Decision(BaseModel):
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    next_questions: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)


class ReasoningResult(BaseModel):
    case_id: str
    evidence: list[Evidence]
    hypotheses: list[Hypothesis]
    decision: Decision


class CaseOutcome(BaseModel):
    outcome: Literal["improved", "unchanged", "worsened", "resolved", "unknown"]
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str = ""
    evidence: list[Observation] = Field(default_factory=list)


class CaseRecord(BaseModel):
    case: FarmCase
    latest_reasoning: ReasoningResult | None = None
    outcome: CaseOutcome | None = None


class FarmSnapshot(BaseModel):
    """Time-bounded view used by reasoning engines.

    The parcel boundary is carried through the snapshot so spatial evidence
    validation does not have to reconstruct farm geometry from provider data.
    """
    farm_id: str
    as_of: datetime
    active_crop: CropCycle
    parcel_location: GeoPoint
    parcel_boundary: list[GeoPoint] = Field(default_factory=list)
    active_parties: list[LandParty] = Field(default_factory=list)
    active_contracts: list[ProductionContract] = Field(default_factory=list)
    recent_observations: list[Observation] = Field(default_factory=list)
