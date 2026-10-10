from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

import ofi.api as api
from ofi.services.evidence_comparison_ledger import InMemoryEvidenceComparisonLedger


client = TestClient(api.app)


def setup_ledger(monkeypatch):
    monkeypatch.setattr(api, "evidence_comparison_ledger", InMemoryEvidenceComparisonLedger())


def policy_request(policy_id, tolerance):
    return {
        "policy_id": policy_id,
        "description": "Synthetic farm weather comparison",
        "max_age_seconds_by_kind": {"weather": 3600},
        "require_provenance": True,
        "comparison_rules": [{
            "rule_id": "temperature-rule",
            "value_key": "temperature_c",
            "observation_kinds": ["weather"],
            "unit": "degC",
            "spatial_scope": "farm",
            "max_time_delta_seconds": 7200,
            "absolute_tolerance": tolerance,
            "require_same_crop_cycle": True,
            "require_distinct_sources": True,
        }],
    }


def create_farm_with_observations():
    suffix = uuid4().hex
    case_id = f"ledger-case-{suffix}"
    farm_id = f"ledger-farm-{suffix}"
    crop_id = f"ledger-crop-{suffix}"
    create = client.post("/cases", json={
        "id": case_id,
        "farm": {
            "id": farm_id,
            "farmer_id": f"farmer-{suffix}",
            "parcel": {
                "id": f"parcel-{suffix}",
                "location": {"latitude": 27.0, "longitude": 72.0},
            },
            "crop_cycle": {"id": crop_id, "crop": "bajra"},
        },
        "query": "evidence-ledger api test",
    })
    assert create.status_code == 200

    now = datetime.now(timezone.utc)
    for suffix_id, source, value in (("a", "station-a", 30), ("b", "station-b", 37)):
        response = client.post(f"/cases/{case_id}/observations", json={
            "id": f"temperature-{suffix_id}-{suffix}",
            "kind": "weather",
            "timestamp": (now - timedelta(minutes=10)).isoformat(),
            "value": {"temperature_c": value},
            "source": source,
            "unit": "degC",
            "spatial_scope": "farm",
            "crop_cycle_id": crop_id,
            "provenance": {"record_id": f"source-{suffix_id}"},
        })
        assert response.status_code == 200
    return farm_id


def test_policy_versions_pin_assessment_to_the_exact_policy_and_snapshot(monkeypatch):
    setup_ledger(monkeypatch)
    policy_id = f"temperature-policy-{uuid4().hex}"

    first = client.post("/evidence-comparison-policies", json=policy_request(policy_id, 2))
    second = client.post("/evidence-comparison-policies", json=policy_request(policy_id, 10))
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2

    latest = client.get(f"/evidence-comparison-policies/{policy_id}")
    historic = client.get(f"/evidence-comparison-policies/{policy_id}?version=1")
    assert latest.json()["version"] == 2
    assert latest.json()["comparison_rules"][0]["absolute_tolerance"] == 10
    assert historic.json()["comparison_rules"][0]["absolute_tolerance"] == 2

    farm_id = create_farm_with_observations()
    assessed = client.post(
        f"/farms/{farm_id}/evidence-comparison-assessments",
        json={"policy_id": policy_id, "policy_version": 1},
    )
    assert assessed.status_code == 200
    body = assessed.json()
    assert body["policy_version"] == 1
    assert len(body["observations_snapshot"]) == 2
    assert len(body["evidence_frame"]["eligible_observation_ids"]) == 2
    assert body["comparisons"][0]["status"] == "disagree"

    retrieved = client.get(f"/evidence-comparison-assessments/{body['assessment_id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == body
    assert retrieved.json()["observations_snapshot"][0]["provenance"]


def test_policy_assessment_routes_fail_closed_for_missing_records(monkeypatch):
    setup_ledger(monkeypatch)
    missing_policy = client.get("/evidence-comparison-policies/not-created")
    assert missing_policy.status_code == 404
    missing_assessment = client.get("/evidence-comparison-assessments/not-created")
    assert missing_assessment.status_code == 404

    invalid_version = client.get("/evidence-comparison-policies/not-created?version=0")
    assert invalid_version.status_code == 422

    missing_farm = client.post(
        "/farms/no-such-farm/evidence-comparison-assessments",
        json={"policy_id": "not-created"},
    )
    assert missing_farm.status_code == 404


def test_policy_creation_rejects_non_unique_rule_ids_and_non_positive_freshness(monkeypatch):
    setup_ledger(monkeypatch)
    duplicate = policy_request(f"bad-policy-{uuid4().hex}", 2)
    duplicate["comparison_rules"].append(dict(duplicate["comparison_rules"][0]))
    response = client.post("/evidence-comparison-policies", json=duplicate)
    assert response.status_code == 422

    zero_window = policy_request(f"zero-policy-{uuid4().hex}", 2)
    zero_window["max_age_seconds_by_kind"]["weather"] = 0
    response = client.post("/evidence-comparison-policies", json=zero_window)
    assert response.status_code == 422
