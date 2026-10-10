from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from ofi.api import app
from ofi.services.case_manager import CaseManager


client = TestClient(app)


def make_case_with_observations():
    suffix = uuid4().hex
    case_id = f"gated-case-{suffix}"
    farm_id = f"gated-farm-{suffix}"
    crop_id = f"gated-crop-{suffix}"
    now = datetime.now(timezone.utc)
    response = client.post("/cases", json={
        "id": case_id,
        "farm": {
            "id": farm_id,
            "farmer_id": f"farmer-{suffix}",
            "parcel": {"id": f"parcel-{suffix}", "location": {"latitude": 27, "longitude": 72}},
            "crop_cycle": {"id": crop_id, "crop": "bajra"},
        },
        "query": "yellowing",
    })
    assert response.status_code == 200
    for oid, stamp, value in [
        (f"fresh-{suffix}", now, {"symptom": "yellowing"}),
        (f"stale-{suffix}", now - timedelta(days=10), {"ndvi_trend": -0.4}),
    ]:
        result = client.post(f"/cases/{case_id}/observations", json={
            "id": oid, "kind": "weather" if oid.startswith("fresh") else "satellite",
            "timestamp": stamp.isoformat(), "value": value, "source": "synthetic",
            "crop_cycle_id": crop_id, "provenance": {"record_id": oid},
        })
        assert result.status_code == 200
    return case_id, farm_id


def test_policy_gated_reasoning_returns_frame_and_excludes_stale_inputs():
    case_id, _ = make_case_with_observations()
    response = client.post(f"/cases/{case_id}/reason-with-evidence-policy", json={
        "max_age_seconds_by_kind": {"weather": 86400, "satellite": 86400},
        "require_provenance": True,
    })
    assert response.status_code == 200
    body = response.json()
    frame = body["evidence_frame"]
    assert len(frame["eligible_observation_ids"]) == 1
    assessments = {item["freshness"]: item for item in frame["assessments"]}
    assert assessments["fresh"]["eligible"] is True
    assert assessments["stale"]["eligible"] is False
    assert "observation_stale" in assessments["stale"]["issues"]
    # Reasoning evidence can only cite the eligible observation.
    reasoning = body["record"]["latest_reasoning"]
    used_ids = {oid for evidence in reasoning["evidence"] for oid in evidence["observation_ids"]}
    assert used_ids <= set(frame["eligible_observation_ids"])


def test_policy_gated_reasoning_rejects_unknown_case_and_invalid_policy():
    missing = client.post("/cases/no-such-case/reason-with-evidence-policy", json={
        "max_age_seconds_by_kind": {"weather": 86400},
    })
    assert missing.status_code == 404
    case_id, _ = make_case_with_observations()
    invalid = client.post(f"/cases/{case_id}/reason-with-evidence-policy", json={
        "max_age_seconds_by_kind": {"weather": 0},
    })
    assert invalid.status_code == 422
