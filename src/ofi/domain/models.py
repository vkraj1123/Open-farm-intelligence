from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class GeoPoint(BaseModel):
    latitude: float
    longitude: float


class Parcel(BaseModel):
    id: str
    location: GeoPoint
    area_ha: float | None = None


class CropCycle(BaseModel):
    crop: str
    season: str | None = None
    sowing_date: date | None = None


class Farm(BaseModel):
    id: str
    farmer_id: str
    parcel: Parcel
    crop_cycle: CropCycle


ObservationKind = Literal[
    "farmer_report", "image", "soil", "weather", "satellite", "market", "sensor"
]


class Observation(BaseModel):
    id: str
    kind: ObservationKind
    timestamp: datetime
    value: dict[str, Any]
    source: str
    quality: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


CaseStatus = Literal[
    "reported", "triaged", "actioned", "observing", "resolved", "escalated"
]


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
    status: CaseStatus = "reported"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[CaseEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timestamps(self):
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


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


Action = Literal[
    "ADVISE", "ASK_FARMER", "REQUEST_TEST", "ESCALATE_EXPERT", "ROUTE_SERVICE"
]


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
