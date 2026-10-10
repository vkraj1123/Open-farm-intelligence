from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from ofi.api import app


client = TestClient(app)


def create_farm():
    suffix = uuid4().hex
    case_id = f"comparison-case-{suffix}"
    farm_id = f"comparison-farm-{suffix}"
    crop_id = f"comparison-crop-{suffix}"
    created = client.post("/cases", json={
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
        "query": "synthetic evidence comparison",
    })
    assert created.status_code == 200

    now = datetime.now(timezone.utc)
    for oid, source, value in [
        (f"temperature-a-{suffix}", "station-a", 30),
        (f"temperature-b-{suffix}", "station-b", 37),
    ]:
        response = client.post(f"/cases/{case_id}/observations", json={
            "id": oid,
            "kind": "weather",
            "timestamp": (now - timedelta(minutes=15)).isoformat(),
            "value": {"temperature_c": value},
            "source": source,
            "unit": "degC",
            "spatial_scope": "farm",
            "crop_cycle_id": crop_id,
            "provenance": {"synthetic": True, "record_id": oid},
        })
        assert response.status_code == 200
    return farm_id


def comparison_request():
    return {
        "max_age_seconds_by_kind": {"weather": 3600},
        "require_provenance": True,
        "comparison_rules": [{
            "rule_id": "temperature-rule",
            "value_key": "temperature_c",
            "observation_kinds": ["weather"],
            "unit": "degC",
            "spatial_scope": "farm",
            "max_time_delta_seconds": 7200,
            "absolute_tolerance": 2,
        }],
    }


def test_evidence_comparisons_api_returns_disagreement_and_traceability():
    farm_id = create_farm()
    response = client.post(
        f"/farms/{farm_id}/evidence-comparisons",
        json=comparison_request(),
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["eligible_observation_ids"]) == 2
    assert len(body["comparisons"]) == 1
    comparison = body["comparisons"][0]
    assert comparison["status"] == "disagree"
    assert comparison["absolute_difference"] == 7
    assert comparison["reasons"] == ["difference_exceeds_absolute_tolerance"]
    assert comparison["left_source"] != comparison["right_source"]
    assert body["assessments"]


def test_evidence_comparisons_api_rejects_unknown_kind_and_unknown_farm():
    bad_request = comparison_request()
    bad_request["comparison_rules"][0]["observation_kinds"] = ["not-a-kind"]
    invalid = client.post("/farms/missing-farm/evidence-comparisons", json=bad_request)
    assert invalid.status_code == 422

    missing = client.post(
        "/farms/missing-farm/evidence-comparisons",
        json=comparison_request(),
    )
    assert missing.status_code == 404


def test_evidence_comparisons_api_rejects_duplicate_rule_ids():
    farm_id = create_farm()
    body = comparison_request()
    body["comparison_rules"].append(dict(body["comparison_rules"][0]))
    response = client.post(f"/farms/{farm_id}/evidence-comparisons", json=body)
    assert response.status_code == 422
