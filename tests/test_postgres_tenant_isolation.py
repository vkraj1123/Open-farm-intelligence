"""PostgreSQL integration tests for tenant-scoped persistence boundaries."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from ofi.domain.models import CropCycle, Farm, FarmCase, GeoPoint, Parcel
from ofi.services.consent_repository import PostgresConsentRepository
from ofi.services.evidence_comparison import MetricComparisonRule
from ofi.services.evidence_comparison_ledger import (
    EvidenceComparisonAssessment,
    PostgresEvidenceComparisonLedger,
)
from ofi.services.execution_gateway import ConsentGrant
from ofi.services.postgres_case_repository import PostgresCaseRepository
from ofi.twin.migrate import apply_schema
from ofi.twin.postgis import PostGISFarmTwinStore


DATABASE_URL = os.environ.get("OFI_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires PostgreSQL CI service via OFI_POSTGRES_DSN",
)


def farm_fixture(*, farm_id, farmer_id, tenant_id, parcel_id=None, crop_id=None):
    return Farm(
        id=farm_id,
        farmer_id=farmer_id,
        tenant_id=tenant_id,
        parcel=Parcel(
            id=parcel_id or f"parcel-{uuid4().hex}",
            location=GeoPoint(latitude=27.0, longitude=72.0),
        ),
        crop_cycle=CropCycle(id=crop_id or f"crop-{uuid4().hex}", crop="bajra"),
    )


def comparison_rule():
    return MetricComparisonRule(
        rule_id="temperature-rule",
        value_key="temperature_c",
        observation_kinds=("weather",),
        unit="degC",
        spatial_scope="farm",
        max_time_delta=timedelta(hours=1),
        absolute_tolerance=2,
    )


def test_postgres_farm_case_policy_assessment_and_consent_are_tenant_scoped():
    assert apply_schema(DATABASE_URL) >= 4
    suffix = uuid4().hex
    tenant_a = f"tenant-a-{suffix}"
    tenant_b = f"tenant-b-{suffix}"
    farmer_id = f"farmer-{suffix}"
    farm_id = f"farm-{suffix}"
    case_id = f"case-{suffix}"

    connections = lambda: psycopg.connect(DATABASE_URL)
    farm_store = PostGISFarmTwinStore(connections)
    farm = farm_fixture(
        farm_id=farm_id,
        farmer_id=farmer_id,
        tenant_id=tenant_a,
    )
    farm_store.upsert(farm)
    assert farm_store.get(farm_id).tenant_id == tenant_a

    # A globally colliding farm ID cannot silently move to another tenant,
    # even when a caller reaches the repository below the HTTP/API boundary.
    takeover = farm_fixture(
        farm_id=farm_id,
        farmer_id=f"attacker-{suffix}",
        tenant_id=tenant_b,
    )
    with pytest.raises(ValueError, match="different tenant or owner"):
        farm_store.upsert(takeover)
    assert farm_store.get(farm_id).tenant_id == tenant_a
    assert farm_store.get(farm_id).farmer_id == farmer_id

    cases = PostgresCaseRepository(connections)
    cases.create(FarmCase(id=case_id, farm=farm, query="tenant boundary fixture"))
    assert cases.get(case_id, tenant_id=tenant_a).case.farm.tenant_id == tenant_a
    with pytest.raises(KeyError):
        cases.get(case_id, tenant_id=tenant_b)

    ledger = PostgresEvidenceComparisonLedger(connections)
    policy_id = f"shared-policy-name-{suffix}"
    policy_a = ledger.create_policy_version(
        policy_id,
        description="tenant A",
        max_age_seconds_by_kind={"weather": 3600},
        require_provenance=False,
        comparison_rules=[comparison_rule()],
        tenant_id=tenant_a,
    )
    policy_b = ledger.create_policy_version(
        policy_id,
        description="tenant B",
        max_age_seconds_by_kind={"weather": 3600},
        require_provenance=True,
        comparison_rules=[comparison_rule()],
        tenant_id=tenant_b,
    )
    assert policy_a.version == policy_b.version == 1
    assert ledger.get_policy(policy_id, tenant_id=tenant_a).description == "tenant A"
    assert ledger.get_policy(policy_id, tenant_id=tenant_b).description == "tenant B"

    assessment = EvidenceComparisonAssessment(
        assessment_id=f"assessment-{suffix}",
        policy_id=policy_id,
        policy_version=1,
        farm_id=farm_id,
        crop_cycle_id=farm.crop_cycle.id,
        as_of=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        observations_snapshot=[],
        evidence_frame={},
        comparisons=[],
        tenant_id=tenant_a,
    )
    ledger.save_assessment(assessment)
    assert ledger.get_assessment(assessment.assessment_id, tenant_id=tenant_a) == assessment
    with pytest.raises(KeyError):
        ledger.get_assessment(assessment.assessment_id, tenant_id=tenant_b)

    consents = PostgresConsentRepository(connections)
    grant = ConsentGrant(
        actor_id=farmer_id,
        tenant_id=tenant_a,
        purpose="soil-test referral",
        status="granted",
        granted_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        scopes=("soil_test",),
    )
    consents.create(grant)
    assert consents.get(grant.consent_id, tenant_id=tenant_a) == grant
    with pytest.raises(KeyError, match="not found"):
        consents.get(grant.consent_id, tenant_id=tenant_b)
    with pytest.raises(KeyError, match="not found"):
        consents.revoke(
            grant.consent_id, actor_id=farmer_id, tenant_id=tenant_b
        )
