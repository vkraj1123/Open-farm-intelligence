"""Authenticated actor/resource ownership tests for farm and case API routes."""
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import ofi.api as api
from ofi.services.case_manager import CaseManager
from ofi.services.evidence_comparison_ledger import InMemoryEvidenceComparisonLedger


SECRET = "resource-api-test-secret-that-is-definitely-over-32-bytes"
ISSUER = "ofi-resource-test-issuer"
AUDIENCE = "ofi-resource-test-api"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def make_token(
    actor_id: str,
    role: str = "farmer",
    tenant_id: str = "tenant-default",
) -> str:
    now = time.time()
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "sub": actor_id,
        "role": role,
        "tenant_id": tenant_id,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 3600,
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
    encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"


def auth(
    actor_id: str,
    role: str = "farmer",
    tenant_id: str = "tenant-default",
) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(actor_id, role, tenant_id)}"}


@pytest.fixture
def resource_client(monkeypatch):
    monkeypatch.setenv("OFI_RESOURCE_AUTH_ENABLED", "true")
    monkeypatch.setenv("OFI_AUTH_HMAC_SECRET", SECRET)
    monkeypatch.setenv("OFI_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("OFI_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(api, "case_manager", CaseManager())
    monkeypatch.setattr(api, "evidence_comparison_ledger", InMemoryEvidenceComparisonLedger())
    with TestClient(api.app) as client:
        yield client


def case_payload(*, case_id: str, farm_id: str, farmer_id: str, parties=None):
    suffix = uuid4().hex
    return {
        "id": case_id,
        "farm": {
            "id": farm_id,
            "farmer_id": farmer_id,
            "parcel": {
                "id": f"parcel-{suffix}",
                "location": {"latitude": 27.0, "longitude": 72.0},
            },
            "crop_cycle": {"id": f"crop-{suffix}", "crop": "bajra"},
            "parties": parties or [],
        },
        "query": "resource-ownership regression fixture",
    }


def create_case(
    client,
    body,
    *,
    actor_id: str,
    role: str = "farmer",
    tenant_id: str = "tenant-default",
):
    return client.post(
        "/cases", headers=auth(actor_id, role, tenant_id), json=body
    )


def test_farm_and_case_routes_require_verified_bearer_tokens(resource_client):
    client = resource_client
    assert client.get("/cases/unknown-case").status_code == 401
    assert client.post(
        "/farms/unknown-farm/evidence-frame",
        json={"max_age_seconds_by_kind": {"weather": 3600}},
    ).status_code == 401


def test_case_and_farm_data_are_hidden_from_other_actors(resource_client):
    client = resource_client
    farmer_id = f"farmer-{uuid4().hex}"
    case_id = f"case-{uuid4().hex}"
    farm_id = f"farm-{uuid4().hex}"
    created = create_case(
        client,
        case_payload(case_id=case_id, farm_id=farm_id, farmer_id=farmer_id),
        actor_id=farmer_id,
    )
    assert created.status_code == 200

    stranger = auth(f"stranger-{uuid4().hex}")
    assert client.get(f"/cases/{case_id}", headers=stranger).status_code == 404
    observation = {
        "id": f"obs-{uuid4().hex}",
        "kind": "weather",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "value": {"temperature_c": 30},
        "source": "test",
    }
    denied_update = client.post(
        f"/cases/{case_id}/observations",
        headers=stranger,
        json=observation,
    )
    assert denied_update.status_code == 404
    denied_farm_read = client.post(
        f"/farms/{farm_id}/evidence-frame",
        headers=stranger,
        json={"max_age_seconds_by_kind": {"weather": 3600}},
    )
    assert denied_farm_read.status_code == 404

    assert client.get(f"/cases/{case_id}", headers=auth(farmer_id)).status_code == 200


def test_case_creation_cannot_take_over_an_existing_farm_id(resource_client):
    client = resource_client
    farmer_id = f"farmer-{uuid4().hex}"
    case_id = f"case-{uuid4().hex}"
    farm_id = f"farm-{uuid4().hex}"
    original = case_payload(case_id=case_id, farm_id=farm_id, farmer_id=farmer_id)
    assert create_case(client, original, actor_id=farmer_id).status_code == 200

    attacker_id = f"attacker-{uuid4().hex}"
    takeover = case_payload(
        case_id=f"case-{uuid4().hex}",
        farm_id=farm_id,
        farmer_id=attacker_id,
    )
    result = create_case(client, takeover, actor_id=attacker_id)
    assert result.status_code == 404

    persisted = client.get(f"/cases/{case_id}", headers=auth(farmer_id))
    assert persisted.status_code == 200
    assert persisted.json()["case"]["farm"]["farmer_id"] == farmer_id


@pytest.mark.parametrize(
    ("verification", "valid_to", "expected_status"),
    [
        ("verified", None, 200),
        ("declared", None, 404),
        ("verified", "2000-01-01", 404),
    ],
)
def test_only_current_verified_owner_parties_can_access_cases(
    resource_client, verification, valid_to, expected_status
):
    client = resource_client
    primary_farmer = f"farmer-{uuid4().hex}"
    owner_id = f"owner-{uuid4().hex}"
    case_id = f"case-{uuid4().hex}"
    farm_id = f"farm-{uuid4().hex}"
    party = {
        "party_id": owner_id,
        "role": "owner",
        "valid_from": "2000-01-01",
        "valid_to": valid_to,
        "verification": verification,
    }
    created = create_case(
        client,
        case_payload(
            case_id=case_id,
            farm_id=farm_id,
            farmer_id=primary_farmer,
            parties=[party],
        ),
        actor_id=primary_farmer,
    )
    assert created.status_code == 200

    result = client.get(f"/cases/{case_id}", headers=auth(owner_id, role="owner"))
    assert result.status_code == expected_status


def test_only_trusted_roles_can_create_global_comparison_policies(resource_client):
    client = resource_client
    farmer = auth(f"farmer-{uuid4().hex}", role="farmer")
    payload = {
        "policy_id": f"policy-{uuid4().hex}",
        "description": "test policy",
        "max_age_seconds_by_kind": {"weather": 3600},
        "require_provenance": False,
        "comparison_rules": [{
            "rule_id": "temperature-rule",
            "value_key": "temperature_c",
            "observation_kinds": ["weather"],
            "unit": "degC",
            "spatial_scope": "farm",
            "max_time_delta_seconds": 3600,
            "absolute_tolerance": 2,
            "require_same_crop_cycle": True,
            "require_distinct_sources": True,
        }],
    }
    assert client.post(
        "/evidence-comparison-policies", headers=farmer, json=payload
    ).status_code == 403
    trusted = auth(f"service-{uuid4().hex}", role="service_provider")
    assert client.post(
        "/evidence-comparison-policies", headers=trusted, json=payload
    ).status_code == 200



def test_same_actor_cannot_cross_tenant_boundary(resource_client):
    client = resource_client
    actor_id = f"farmer-{uuid4().hex}"
    case_id = f"case-{uuid4().hex}"
    farm_id = f"farm-{uuid4().hex}"
    body = case_payload(
        case_id=case_id, farm_id=farm_id, farmer_id=actor_id
    )
    created = create_case(
        client, body, actor_id=actor_id, tenant_id="tenant-b"
    )
    assert created.status_code == 200

    assert client.get(
        f"/cases/{case_id}", headers=auth(actor_id, tenant_id="tenant-a")
    ).status_code == 404
    assert client.get(
        f"/cases/{case_id}", headers=auth(actor_id, tenant_id="tenant-b")
    ).status_code == 200
    denied = client.post(
        f"/farms/{farm_id}/evidence-frame",
        headers=auth(actor_id, tenant_id="tenant-a"),
        json={"max_age_seconds_by_kind": {"weather": 3600}},
    )
    assert denied.status_code == 404


def test_request_cannot_choose_a_different_tenant_than_signed_claim(resource_client):
    client = resource_client
    actor_id = f"farmer-{uuid4().hex}"
    body = case_payload(
        case_id=f"case-{uuid4().hex}",
        farm_id=f"farm-{uuid4().hex}",
        farmer_id=actor_id,
    )
    body["farm"]["tenant_id"] = "tenant-attacker-selected"
    response = create_case(
        client, body, actor_id=actor_id, tenant_id="tenant-verified"
    )
    assert response.status_code == 404


def test_evidence_comparison_policies_are_namespaced_by_tenant(resource_client):
    client = resource_client
    policy_id = f"shared-name-{uuid4().hex}"
    payload = {
        "policy_id": policy_id,
        "description": "tenant isolation fixture",
        "max_age_seconds_by_kind": {"weather": 3600},
        "require_provenance": False,
        "comparison_rules": [{
            "rule_id": "temperature-rule",
            "value_key": "temperature_c",
            "observation_kinds": ["weather"],
            "unit": "degC",
            "spatial_scope": "farm",
            "max_time_delta_seconds": 3600,
            "absolute_tolerance": 2,
            "require_same_crop_cycle": True,
            "require_distinct_sources": True,
        }],
    }
    tenant_a = auth(f"svc-a-{uuid4().hex}", role="service_provider", tenant_id="tenant-a")
    tenant_b = auth(f"svc-b-{uuid4().hex}", role="service_provider", tenant_id="tenant-b")
    created_a = client.post("/evidence-comparison-policies", headers=tenant_a, json=payload)
    created_b = client.post("/evidence-comparison-policies", headers=tenant_b, json=payload)
    assert created_a.status_code == 200
    assert created_b.status_code == 200
    assert created_a.json()["version"] == created_b.json()["version"] == 1

    read_a = client.get(f"/evidence-comparison-policies/{policy_id}", headers=tenant_a)
    read_b = client.get(f"/evidence-comparison-policies/{policy_id}", headers=tenant_b)
    assert read_a.status_code == read_b.status_code == 200
    assert read_a.json()["policy_id"] == read_b.json()["policy_id"] == policy_id
    assert client.get(
        f"/evidence-comparison-policies/{policy_id}",
        headers=auth("svc-a", role="service_provider", tenant_id="tenant-c"),
    ).status_code == 404


def test_cross_tenant_consent_lookup_is_hidden(resource_client, monkeypatch):
    # Consent storage is also tenant-bound; separate API fixture keeps the
    # purpose of this regression explicit.
    import ofi.api as api
    from ofi.services.consent_repository import InMemoryConsentRepository
    from fastapi.testclient import TestClient
    from tests.test_authenticated_consent_api import make_token as consent_token

    store = InMemoryConsentRepository()
    monkeypatch.setattr(api, "consent_repository", store)
    client = resource_client
    actor_id = f"farmer-{uuid4().hex}"
    tenant_a = auth(actor_id, tenant_id="tenant-a")
    # The client token helper above uses the resource test issuer, so create a
    # grant directly with the verified subject/tenant match through the API's
    # public consent route would require switching to the consent test issuer.
    from ofi.services.execution_gateway import ConsentGrant
    grant = ConsentGrant(
        actor_id=actor_id,
        tenant_id="tenant-b",
        purpose="cross tenant test",
        status="granted",
        granted_at=datetime.now(timezone.utc),
        scopes=("soil_test",),
    )
    store.create(grant)
    assert client.get(
        f"/consents/{grant.consent_id}", headers=tenant_a
    ).status_code == 404
