"""Versioned comparison policies and immutable evidence-assessment history.

The in-memory implementation supports tests and single-process development.
PostgresEvidenceComparisonLedger is the durable implementation for deployments
configured with PostgreSQL. Policies are versioned by creating new immutable
versions; assessment rows preserve the inputs and outputs used for each run.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from threading import RLock
from typing import Any, Callable, Mapping, Sequence

from ofi.services.db_context import connection_scope
from ofi.services.evidence_comparison import MetricComparisonRule


@dataclass(frozen=True)
class EvidenceComparisonPolicyVersion:
    policy_id: str
    version: int
    description: str
    max_age_seconds_by_kind: dict[str, float]
    require_provenance: bool
    comparison_rules: tuple[MetricComparisonRule, ...]
    created_at: datetime
    tenant_id: str = "legacy:default"


@dataclass(frozen=True)
class EvidenceComparisonAssessment:
    assessment_id: str
    policy_id: str
    policy_version: int
    farm_id: str
    crop_cycle_id: str
    as_of: datetime
    recorded_at: datetime
    observations_snapshot: list[dict[str, Any]]
    evidence_frame: dict[str, Any]
    comparisons: list[dict[str, Any]]
    tenant_id: str = "legacy:default"


def _rule_to_payload(rule: MetricComparisonRule) -> dict[str, Any]:
    return {
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


def _rule_from_payload(payload: Mapping[str, Any]) -> MetricComparisonRule:
    return MetricComparisonRule(
        rule_id=payload["rule_id"],
        value_key=payload["value_key"],
        observation_kinds=tuple(payload["observation_kinds"]),
        unit=payload["unit"],
        spatial_scope=payload["spatial_scope"],
        max_time_delta=timedelta(seconds=float(payload["max_time_delta_seconds"])),
        absolute_tolerance=float(payload["absolute_tolerance"]),
        require_same_crop_cycle=bool(payload.get("require_same_crop_cycle", True)),
        require_distinct_sources=bool(payload.get("require_distinct_sources", True)),
    )


def _validate_policy(
    policy_id: str,
    description: str,
    max_age_seconds_by_kind: Mapping[str, float],
    comparison_rules: Sequence[MetricComparisonRule],
) -> None:
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise ValueError("policy_id must not be blank")
    if not isinstance(description, str):
        raise ValueError("description must be a string")
    if not max_age_seconds_by_kind:
        raise ValueError("max_age_seconds_by_kind must not be empty")
    for kind, seconds in max_age_seconds_by_kind.items():
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("observation kind must not be blank")
        if isinstance(seconds, bool) or not isinstance(seconds, (float, int)):
            raise ValueError(f"freshness window for {kind!r} must be numeric")
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"freshness window for {kind!r} must be finite and positive")
        try:
            timedelta(seconds=seconds)
        except OverflowError as exc:
            raise ValueError(f"freshness window for {kind!r} is too large") from exc
    if not comparison_rules:
        raise ValueError("comparison_rules must not be empty")
    rule_ids = [rule.rule_id for rule in comparison_rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("comparison rule IDs must be unique")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _jsonb_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _copy_policy(policy: EvidenceComparisonPolicyVersion) -> EvidenceComparisonPolicyVersion:
    return deepcopy(policy)


def _copy_assessment(
    assessment: EvidenceComparisonAssessment,
) -> EvidenceComparisonAssessment:
    return deepcopy(assessment)


class EvidenceComparisonLedger(ABC):
    """Storage boundary for immutable policy versions and assessment records."""

    @abstractmethod
    def create_policy_version(
        self,
        policy_id: str,
        *,
        description: str,
        max_age_seconds_by_kind: Mapping[str, float],
        require_provenance: bool,
        comparison_rules: Sequence[MetricComparisonRule],
        tenant_id: str = "legacy:default",
    ) -> EvidenceComparisonPolicyVersion:
        raise NotImplementedError

    @abstractmethod
    def get_policy(
        self,
        policy_id: str,
        version: int | None = None,
        *,
        tenant_id: str = "legacy:default",
    ) -> EvidenceComparisonPolicyVersion:
        raise NotImplementedError

    @abstractmethod
    def save_assessment(
        self, assessment: EvidenceComparisonAssessment
    ) -> EvidenceComparisonAssessment:
        raise NotImplementedError

    @abstractmethod
    def get_assessment(
        self, assessment_id: str, tenant_id: str = "legacy:default"
    ) -> EvidenceComparisonAssessment:
        raise NotImplementedError


class InMemoryEvidenceComparisonLedger(EvidenceComparisonLedger):
    """Thread-safe, process-local reference store; not durable across restarts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._policy_versions: dict[tuple[str, str], dict[int, EvidenceComparisonPolicyVersion]] = {}
        self._assessments: dict[str, EvidenceComparisonAssessment] = {}

    def create_policy_version(
        self,
        policy_id: str,
        *,
        description: str,
        max_age_seconds_by_kind: Mapping[str, float],
        require_provenance: bool,
        comparison_rules: Sequence[MetricComparisonRule],
        tenant_id: str = "legacy:default",
    ) -> EvidenceComparisonPolicyVersion:
        _validate_policy(policy_id, description, max_age_seconds_by_kind, comparison_rules)
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must not be blank")
        tenant_id = tenant_id.strip()
        with self._lock:
            versions = self._policy_versions.setdefault((tenant_id, policy_id), {})
            version = max(versions, default=0) + 1
            policy = EvidenceComparisonPolicyVersion(
                policy_id=policy_id,
                version=version,
                description=description,
                max_age_seconds_by_kind=dict(max_age_seconds_by_kind),
                require_provenance=bool(require_provenance),
                comparison_rules=tuple(deepcopy(tuple(comparison_rules))),
                created_at=datetime.now(timezone.utc),
                tenant_id=tenant_id,
            )
            versions[version] = _copy_policy(policy)
            return _copy_policy(policy)

    def get_policy(
        self,
        policy_id: str,
        version: int | None = None,
        *,
        tenant_id: str = "legacy:default",
    ) -> EvidenceComparisonPolicyVersion:
        with self._lock:
            versions = self._policy_versions.get((tenant_id, policy_id))
            if not versions:
                raise KeyError(policy_id)
            selected = max(versions) if version is None else version
            try:
                return _copy_policy(versions[selected])
            except KeyError as exc:
                raise KeyError(f"{policy_id}@{selected}") from exc

    def save_assessment(
        self, assessment: EvidenceComparisonAssessment
    ) -> EvidenceComparisonAssessment:
        with self._lock:
            if assessment.assessment_id in self._assessments:
                raise ValueError(f"assessment already exists: {assessment.assessment_id}")
            policy_versions = self._policy_versions.get(
                (assessment.tenant_id, assessment.policy_id), {}
            )
            if assessment.policy_version not in policy_versions:
                raise KeyError(
                    f"{assessment.policy_id}@{assessment.policy_version}"
                )
            stored = _copy_assessment(assessment)
            self._assessments[assessment.assessment_id] = stored
            return _copy_assessment(stored)

    def get_assessment(
        self, assessment_id: str, tenant_id: str = "legacy:default"
    ) -> EvidenceComparisonAssessment:
        with self._lock:
            try:
                assessment = self._assessments[assessment_id]
            except KeyError as exc:
                raise KeyError(assessment_id) from exc
            if assessment.tenant_id != tenant_id:
                raise KeyError(assessment_id)
            return _copy_assessment(assessment)


class PostgresEvidenceComparisonLedger(EvidenceComparisonLedger):
    """Durable PostgreSQL implementation with serialized version allocation."""

    def __init__(self, connection_factory: Callable[[], Any]):
        self._connection_factory = connection_factory

    def create_policy_version(
        self,
        policy_id: str,
        *,
        description: str,
        max_age_seconds_by_kind: Mapping[str, float],
        require_provenance: bool,
        comparison_rules: Sequence[MetricComparisonRule],
        tenant_id: str = "legacy:default",
    ) -> EvidenceComparisonPolicyVersion:
        _validate_policy(policy_id, description, max_age_seconds_by_kind, comparison_rules)
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must not be blank")
        tenant_id = tenant_id.strip()
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO evidence_comparison_policy_heads
                          (tenant_id, policy_id, current_version)
                        VALUES (%s, %s, 0)
                        ON CONFLICT (tenant_id, policy_id) DO NOTHING
                        """,
                        (tenant_id, policy_id),
                    )
                    cur.execute(
                        """
                        SELECT current_version
                        FROM evidence_comparison_policy_heads
                        WHERE tenant_id = %s AND policy_id = %s
                        FOR UPDATE
                        """,
                        (tenant_id, policy_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise RuntimeError(f"policy head was not created: {policy_id}")
                    version = int(row[0]) + 1
                    created_at = datetime.now(timezone.utc)
                    rules_payload = [_rule_to_payload(rule) for rule in comparison_rules]
                    cur.execute(
                        """
                        INSERT INTO evidence_comparison_policy_versions
                          (tenant_id, policy_id, version, description, max_age_seconds_by_kind,
                           require_provenance, comparison_rules, created_at)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s)
                        """,
                        (
                            tenant_id,
                            policy_id,
                            version,
                            description,
                            _json(dict(max_age_seconds_by_kind)),
                            bool(require_provenance),
                            _json(rules_payload),
                            created_at,
                        ),
                    )
                    cur.execute(
                        """
                        UPDATE evidence_comparison_policy_heads
                        SET current_version = %s
                        WHERE tenant_id = %s AND policy_id = %s
                        """,
                        (version, tenant_id, policy_id),
                    )
        return EvidenceComparisonPolicyVersion(
            policy_id=policy_id,
            version=version,
            description=description,
            max_age_seconds_by_kind=dict(max_age_seconds_by_kind),
            require_provenance=bool(require_provenance),
            comparison_rules=tuple(deepcopy(tuple(comparison_rules))),
            created_at=created_at,
            tenant_id=tenant_id,
        )

    def get_policy(
        self,
        policy_id: str,
        version: int | None = None,
        *,
        tenant_id: str = "legacy:default",
    ) -> EvidenceComparisonPolicyVersion:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                if version is None:
                    cur.execute(
                        """
                        SELECT p.policy_id, p.version, p.description,
                               p.max_age_seconds_by_kind, p.require_provenance,
                               p.comparison_rules, p.created_at
                        FROM evidence_comparison_policy_versions p
                        JOIN evidence_comparison_policy_heads h
                          ON h.policy_id = p.policy_id
                         AND h.current_version = p.version
                        WHERE p.tenant_id = %s AND p.policy_id = %s
                        """,
                        (tenant_id, policy_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT policy_id, version, description,
                               max_age_seconds_by_kind, require_provenance,
                               comparison_rules, created_at
                        FROM evidence_comparison_policy_versions
                        WHERE tenant_id = %s AND policy_id = %s AND version = %s
                        """,
                        (tenant_id, policy_id, version),
                    )
                row = cur.fetchone()
                if row is None:
                    lookup = policy_id if version is None else f"{policy_id}@{version}"
                    raise KeyError(lookup)
                (
                    selected_id,
                    selected_version,
                    description,
                    max_age,
                    require_provenance,
                    rules,
                    created_at,
                ) = row
                return EvidenceComparisonPolicyVersion(
                    policy_id=selected_id,
                    version=int(selected_version),
                    description=description,
                    max_age_seconds_by_kind=dict(_jsonb_value(max_age)),
                    require_provenance=bool(require_provenance),
                    comparison_rules=tuple(
                        _rule_from_payload(item) for item in _jsonb_value(rules)
                    ),
                    created_at=created_at,
                    tenant_id=tenant_id,
                )

    def save_assessment(
        self, assessment: EvidenceComparisonAssessment
    ) -> EvidenceComparisonAssessment:
        self._validate_assessment(assessment)
        with connection_scope(self._connection_factory) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO evidence_comparison_assessments
                          (assessment_id, tenant_id, policy_id, policy_version, farm_id,
                           crop_cycle_id, as_of, recorded_at, observations_snapshot,
                           evidence_frame, comparisons)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                                %s::jsonb, %s::jsonb, %s::jsonb)
                        """,
                        (
                            assessment.assessment_id,
                            assessment.tenant_id,
                            assessment.policy_id,
                            assessment.policy_version,
                            assessment.farm_id,
                            assessment.crop_cycle_id,
                            assessment.as_of,
                            assessment.recorded_at,
                            _json(assessment.observations_snapshot),
                            _json(assessment.evidence_frame),
                            _json(assessment.comparisons),
                        ),
                    )
        return _copy_assessment(assessment)

    def get_assessment(
        self, assessment_id: str, tenant_id: str = "legacy:default"
    ) -> EvidenceComparisonAssessment:
        with connection_scope(self._connection_factory) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT assessment_id, policy_id, policy_version, farm_id,
                           crop_cycle_id, as_of, recorded_at, observations_snapshot,
                           evidence_frame, comparisons
                    FROM evidence_comparison_assessments
                    WHERE assessment_id = %s AND tenant_id = %s
                    """,
                    (assessment_id, tenant_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(assessment_id)
                (
                    selected_id,
                    policy_id,
                    policy_version,
                    farm_id,
                    crop_cycle_id,
                    as_of,
                    recorded_at,
                    observations,
                    evidence_frame,
                    comparisons,
                ) = row
                return EvidenceComparisonAssessment(
                    assessment_id=selected_id,
                    policy_id=policy_id,
                    policy_version=int(policy_version),
                    farm_id=farm_id,
                    crop_cycle_id=crop_cycle_id,
                    as_of=as_of,
                    recorded_at=recorded_at,
                    observations_snapshot=list(_jsonb_value(observations)),
                    evidence_frame=dict(_jsonb_value(evidence_frame)),
                    comparisons=list(_jsonb_value(comparisons)),
                    tenant_id=tenant_id,
                )

    @staticmethod
    def _validate_assessment(assessment: EvidenceComparisonAssessment) -> None:
        for name in ("assessment_id", "policy_id", "farm_id", "crop_cycle_id", "tenant_id"):
            value = getattr(assessment, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        if assessment.policy_version < 1:
            raise ValueError("policy_version must be positive")
        if assessment.as_of.tzinfo is None or assessment.as_of.utcoffset() is None:
            raise ValueError("assessment as_of must be timezone-aware")
        if (
            assessment.recorded_at.tzinfo is None
            or assessment.recorded_at.utcoffset() is None
        ):
            raise ValueError("assessment recorded_at must be timezone-aware")
        # Reject unsupported numeric values before reaching PostgreSQL JSONB.
        _json(assessment.observations_snapshot)
        _json(assessment.evidence_frame)
        _json(assessment.comparisons)
