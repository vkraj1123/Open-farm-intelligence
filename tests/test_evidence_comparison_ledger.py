from datetime import datetime, timedelta, timezone

import pytest

from ofi.services.evidence_comparison import MetricComparisonRule
from ofi.services.evidence_comparison_ledger import (
    EvidenceComparisonAssessment,
    InMemoryEvidenceComparisonLedger,
)


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def make_rule(tolerance=2.0):
    return MetricComparisonRule(
        rule_id="temperature-v1",
        value_key="temperature_c",
        observation_kinds=("weather",),
        unit="degC",
        spatial_scope="farm",
        max_time_delta=timedelta(hours=2),
        absolute_tolerance=tolerance,
    )


def make_assessment(assessment_id="assessment-1", policy_version=1):
    return EvidenceComparisonAssessment(
        assessment_id=assessment_id,
        policy_id="farm-weather-policy",
        policy_version=policy_version,
        farm_id="farm-1",
        crop_cycle_id="crop-kharif-2026",
        as_of=NOW,
        recorded_at=NOW + timedelta(seconds=1),
        observations_snapshot=[
            {
                "id": "weather-a",
                "source": "station-a",
                "value": {"temperature_c": 30},
                "provenance": {"record_id": "source-a"},
            },
            {
                "id": "weather-b",
                "source": "station-b",
                "value": {"temperature_c": 37},
                "provenance": {"record_id": "source-b"},
            },
        ],
        evidence_frame={
            "eligible_observation_ids": ["weather-a", "weather-b"],
            "excluded_counts": {},
        },
        comparisons=[
            {
                "rule_id": "temperature-v1",
                "left_observation_id": "weather-a",
                "right_observation_id": "weather-b",
                "status": "disagree",
                "absolute_difference": 7.0,
                "reasons": ["difference_exceeds_absolute_tolerance"],
            }
        ],
    )


def create_policy(ledger, *, tolerance=2.0, policy_id="farm-weather-policy"):
    return ledger.create_policy_version(
        policy_id,
        description="Synthetic temperature comparison",
        max_age_seconds_by_kind={"weather": 3600},
        require_provenance=True,
        comparison_rules=[make_rule(tolerance)],
    )


def test_policy_versions_are_append_only_and_exact_versions_remain_retrievable():
    ledger = InMemoryEvidenceComparisonLedger()
    first = create_policy(ledger, tolerance=2.0)
    second = create_policy(ledger, tolerance=10.0)

    assert first.version == 1
    assert second.version == 2
    assert ledger.get_policy(first.policy_id).version == 2
    assert ledger.get_policy(first.policy_id, 1).comparison_rules[0].absolute_tolerance == 2.0
    assert ledger.get_policy(first.policy_id, 2).comparison_rules[0].absolute_tolerance == 10.0


def test_assessment_preserves_policy_version_and_exact_snapshot_across_reads():
    ledger = InMemoryEvidenceComparisonLedger()
    create_policy(ledger, tolerance=2.0)
    create_policy(ledger, tolerance=10.0)

    original = make_assessment(policy_version=1)
    saved = ledger.save_assessment(original)
    original.observations_snapshot[0]["value"]["temperature_c"] = -99
    original.comparisons[0]["status"] = "agree"
    saved.evidence_frame["eligible_observation_ids"].clear()

    loaded = ledger.get_assessment("assessment-1")
    assert loaded.policy_id == "farm-weather-policy"
    assert loaded.policy_version == 1
    assert loaded.observations_snapshot[0]["value"]["temperature_c"] == 30
    assert loaded.evidence_frame["eligible_observation_ids"] == ["weather-a", "weather-b"]
    assert loaded.comparisons[0]["status"] == "disagree"

    loaded.comparisons.clear()
    assert len(ledger.get_assessment("assessment-1").comparisons) == 1


def test_store_rejects_assessment_without_exact_policy_version_and_duplicate_id():
    ledger = InMemoryEvidenceComparisonLedger()
    create_policy(ledger, tolerance=2.0)

    with pytest.raises(KeyError, match="farm-weather-policy@3"):
        ledger.save_assessment(make_assessment("missing-policy-version", policy_version=3))

    assessment = make_assessment()
    ledger.save_assessment(assessment)
    with pytest.raises(ValueError, match="assessment already exists"):
        ledger.save_assessment(assessment)


def test_invalid_policy_windows_and_duplicate_rule_ids_are_rejected():
    ledger = InMemoryEvidenceComparisonLedger()
    with pytest.raises(ValueError, match="finite and positive"):
        ledger.create_policy_version(
            "invalid-policy",
            description="invalid",
            max_age_seconds_by_kind={"weather": 0},
            require_provenance=True,
            comparison_rules=[make_rule()],
        )
    with pytest.raises(ValueError, match="rule IDs must be unique"):
        ledger.create_policy_version(
            "duplicate-rule-policy",
            description="invalid",
            max_age_seconds_by_kind={"weather": 3600},
            require_provenance=True,
            comparison_rules=[make_rule(), make_rule()],
        )
