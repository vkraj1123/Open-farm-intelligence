"""Regression tests for the trusted Auth0 OFI role/tenant claim Action template."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_repo_file(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_auth0_authorization_action_reads_only_admin_controlled_app_metadata():
    action = read_repo_file("deploy/auth0/trusted-role-tenant-claims-action.js")
    assert "event.user.app_metadata" in action
    assert "metadata.ofi_role" in action
    assert "metadata.ofi_tenant_id" in action
    assert "event.user.user_metadata" not in action
    assert "event.user.email" not in action
    assert "event.user.phone_number" not in action


def test_auth0_authorization_action_has_no_default_role_and_matches_ofi_allowlist():
    action = read_repo_file("deploy/auth0/trusted-role-tenant-claims-action.js")
    for role in ("farmer", "cultivator", "owner", "agent", "service_provider", "system"):
        assert f'"{role}"' in action
    assert 'typeof role !== "string" || !ALLOWED_OFI_ROLES.has(role)' in action
    assert 'setCustomClaim(namespace + "role", role)' in action
    assert 'setCustomClaim(namespace + "tenant_id", tenantId.trim())' in action
    assert "api.access.deny" not in action


def test_auth0_authorization_action_requires_a_valid_explicit_namespace():
    action = read_repo_file("deploy/auth0/trusted-role-tenant-claims-action.js")
    assert "OFI_CLAIM_NAMESPACE" in action
    assert 'value.startsWith("https://")' in action
    assert 'value.endsWith("/")' in action
    assert "value.includes(\"?\")" in action
    assert "value.includes(\"#\")" in action
    assert 'if (!validClaimNamespace(namespace))' in action


def test_auth0_authorization_action_documents_api_claim_configuration_and_membership_gate():
    action = read_repo_file("deploy/auth0/trusted-role-tenant-claims-action.js")
    assert "OFI_AUTH_ROLE_CLAIM" in action
    assert "OFI_AUTH_TENANT_CLAIM" in action
    assert "membership registry" in action
    assert "Keep the API in HS256 mode" in action


def test_auth0_role_tenant_action_has_runtime_behavioral_suite():
    workflow1 = read_repo_file(".github/workflows/test.yml")
    workflow2 = read_repo_file(".github/workflows/tests.yml")
    assert "node --test tests/test_auth0_role_tenant_action.js" in workflow1
    assert "node --test tests/test_auth0_role_tenant_action.js" in workflow2
