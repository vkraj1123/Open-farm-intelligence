"""PostgreSQL integration coverage for durable comparison policy/version history.

The dedicated CI database is provisioned by .github/workflows/tests.yml. Local
runs without OFI_POSTGRES_DSN skip this module rather than substituting mocks.
"""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from ofi.services.evidence_comparison import MetricComparisonRule
from ofi.services.evidence_comparison_ledger import (
    EvidenceComparisonAssessment,
    PostgresEvidenceComparisonLedger,
)
from ofi.twin.migrate import apply_schema


DATABASE_URL = os.environ.get("OFI_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires PostgreSQL CI service via OFI_POSTGRES_DSN",
)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def ledger():
    assert apply_schema(DATABASE_URL) >= 4
    return PostgresEvidenceComparisonLedger(lambda: psycopg.connect(DATABASE_URL))


def make_rule(tolerance):
    return MetricComparisonRule(
        rule_id="temperature-v1",
        value_key="temperature_c",
        observation_kinds=("weather",),
        unit="degC",
        spatial_scope="farm",
        max_time_delta=timedelta(hours=2),
        absolute_tolerance=tolerance,
    )


def make_assessment(assessment_id, policy_id, policy_version):
    return EvidenceComparisonAssessment(
        assessment_id=assessment_id,
        policy_id=policy_id,
        policy_version=policy_version,
        farm_id="farm-postgres-integration",
        crop_cycle_id="crop-postgres-integration",
        as_of=NOW,
        recorded_at=NOW + timedelta(seconds=1),
        observations_snapshot=[
            {
                "id": "weather-a",
                "timestamp": NOW.isoformat(),
                "source": "station-a",
                "unit": "degC",
                "value": {"temperature_c": 30},
                "provenance": {"record_id": "source-a"},
            },
            {
                "id": "weather-b",
                "timestamp": NOW.isoformat(),
                "source": "station-b",
                "unit": "degC",
                "value": {"temperature_c": 37},
                "provenance": {"record_id": "source-b"},
            },
        ],
        evidence_frame={
            "as_of": NOW.isoformat(),
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


def create_policy(ledger, policy_id, tolerance):
    return ledger.create_policy_version(
        policy_id,
        description="PostgreSQL integration policy",
        max_age_seconds_by_kind={"weather": 3600},
        require_provenance=True,
        comparison_rules=[make_rule(tolerance)],
    )


def test_postgres_policy_version_and_assessment_round_trip_are_durable(ledger):
    policy_id = f"pg-policy-{uuid4().hex}"
    version1 = create_policy(ledger, policy_id, 2.0)
    version2 = create_policy(ledger, policy_id, 10.0)

    assert (version1.version, version2.version) == (1, 2)
    assert ledger.get_policy(policy_id).version == 2
    assert ledger.get_policy(policy_id, 1).comparison_rules[0].absolute_tolerance == 2.0
    assert ledger.get_policy(policy_id, 2).comparison_rules[0].absolute_tolerance == 10.0

    assessment_id = f"pg-assessment-{uuid4().hex}"
    record = make_assessment(assessment_id, policy_id, 1)
    ledger.save_assessment(record)
    loaded = ledger.get_assessment(assessment_id)

    assert loaded.policy_id == policy_id
    assert loaded.policy_version == 1
    assert loaded.observations_snapshot[0]["provenance"]["record_id"] == "source-a"
    assert loaded.evidence_frame["eligible_observation_ids"] == ["weather-a", "weather-b"]
    assert loaded.comparisons[0]["status"] == "disagree"
    assert loaded.as_of == NOW


def test_postgres_policy_versions_and_assessments_are_append_only(ledger):
    policy_id = f"pg-immutable-policy-{uuid4().hex}"
    policy = create_policy(ledger, policy_id, 2.0)
    assessment_id = f"pg-immutable-assessment-{uuid4().hex}"
    ledger.save_assessment(make_assessment(assessment_id, policy_id, policy.version))

    with pytest.raises(psycopg.Error, match="immutable"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                UPDATE evidence_comparison_policy_versions
                SET description = 'rewritten'
                WHERE policy_id = %s AND version = %s
                """,
                (policy_id, policy.version),
            )

    with pytest.raises(psycopg.Error, match="immutable"):
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                UPDATE evidence_comparison_assessments
                SET comparisons = '[]'::jsonb
                WHERE assessment_id = %s
                """,
                (assessment_id,),
            )


def test_postgres_assessment_rejects_unknown_policy_version(ledger):
    assessment = make_assessment(
        f"pg-orphan-assessment-{uuid4().hex}",
        f"missing-policy-{uuid4().hex}",
        1,
    )
    with pytest.raises(psycopg.Error):
        ledger.save_assessment(assessment)
