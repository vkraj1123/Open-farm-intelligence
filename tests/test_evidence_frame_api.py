from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from ofi.api import app


client = TestClient(app)


def create_case_with_farm():
    suffix = uuid4().hex
    case_id = f"evidence-frame-case-{suffix}"
    farm_id = f"evidence-frame-farm-{suffix}"
    response = client.post(
        "/cases",
        json={
            "id": case_id,
            "farm": {
                "id": farm_id,
                "farmer_id": f"farmer-{suffix}",
                "parcel": {
                    "id": f"parcel-{suffix}",
                    "location": {"latitude": 27.0, "longitude": 72.0},
                },
                "crop_cycle": {"id": f"crop-{suffix}", "crop": "bajra"},
            },
            "query": "evidence alignment API test",
        },
    )
    assert response.status_code == 200
    return case_id, farm_id, f"crop-{suffix}"


def add_observation(case_id, observation_id, crop_cycle_id, timestamp, *, provenance):
    response = client.post(
        f"/cases/{case_id}/observations",
        json={
            "id": observation_id,
            "kind": "weather",
            "timestamp": timestamp.isoformat(),
            "value": {"temperature_c": 34},
            "source": "weather_station",
            "quality": 0.9,
            "confidence": 0.8,
            "crop_cycle_id": crop_cycle_id,
            "provenance": provenance,
        },
    )
    assert response.status_code == 200


def test_evidence_frame_endpoint_returns_eligible_and_excluded_observations():
    case_id, farm_id, crop_cycle_id = create_case_with_farm()
    now = datetime.now(timezone.utc)
    add_observation(
        case_id, f"fresh-{uuid4().hex}", crop_cycle_id,
        now - timedelta(hours=1), provenance={"record_id": "fresh-source"},
    )
    add_observation(
        case_id, f"stale-{uuid4().hex}", crop_cycle_id,
        now - timedelta(days=3), provenance={"record_id": "stale-source"},
    )

    response = client.post(
        f"/farms/{farm_id}/evidence-frame",
        json={"max_age_seconds_by_kind": {"weather": 86400}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["farm_id"] == farm_id
    assert body["crop_cycle_id"] == crop_cycle_id
    assert len(body["eligible_observation_ids"]) == 1
    statuses = {item["freshness"]: item["eligible"] for item in body["assessments"]}
    assert statuses["fresh"] is True
    assert statuses["stale"] is False
    assert body["excluded_counts"]["observation_stale"] == 1


def test_evidence_frame_endpoint_can_require_provenance():
    case_id, farm_id, crop_cycle_id = create_case_with_farm()
    add_observation(
        case_id, f"no-provenance-{uuid4().hex}", crop_cycle_id,
        datetime.now(timezone.utc), provenance={},
    )

    response = client.post(
        f"/farms/{farm_id}/evidence-frame",
        json={
            "max_age_seconds_by_kind": {"weather": 86400},
            "require_provenance": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible_observation_ids"] == []
    assessment = body["assessments"][0]
    assert assessment["eligible"] is False
    assert "provenance_required" in assessment["issues"]


def test_evidence_frame_endpoint_rejects_non_positive_freshness_policy():
    _, farm_id, _ = create_case_with_farm()

    response = client.post(
        f"/farms/{farm_id}/evidence-frame",
        json={"max_age_seconds_by_kind": {"weather": 0}},
    )

    assert response.status_code == 422


def test_evidence_frame_endpoint_returns_404_for_unknown_farm():
    response = client.post(
        "/farms/no-such-farm/evidence-frame",
        json={"max_age_seconds_by_kind": {"weather": 86400}},
    )

    assert response.status_code == 404
