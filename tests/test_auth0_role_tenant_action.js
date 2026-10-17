const test = require("node:test");
const assert = require("node:assert/strict");

const { onExecutePostLogin } = require("../deploy/auth0/trusted-role-tenant-claims-action.js");

const NAMESPACE = "https://claims.example.org/ofi/";

async function execute({ namespace = NAMESPACE, metadata = {} } = {}) {
  const claims = {};
  const event = {
    secrets: namespace === undefined ? {} : { OFI_CLAIM_NAMESPACE: namespace },
    user: { app_metadata: metadata }
  };
  const api = {
    accessToken: {
      setCustomClaim(name, value) {
        assert.equal(Object.hasOwn(claims, name), false, `duplicate claim: ${name}`);
        claims[name] = value;
      }
    }
  };

  await onExecutePostLogin(event, api);
  return claims;
}

test("emits namespaced claims from trusted app_metadata only", async () => {
  const claims = await execute({
    metadata: { ofi_role: "farmer", ofi_tenant_id: "  tenant-17  " }
  });

  assert.deepEqual(claims, {
    "https://claims.example.org/ofi/role": "farmer",
    "https://claims.example.org/ofi/tenant_id": "tenant-17"
  });
});

test("emits each role from OFI's allow-list without altering it", async () => {
  for (const role of ["farmer", "cultivator", "owner", "agent", "service_provider", "system"]) {
    const claims = await execute({ metadata: { ofi_role: role } });
    assert.equal(claims["https://claims.example.org/ofi/role"], role);
  }
});

test("omits the role claim when metadata is absent", async () => {
  assert.deepEqual(await execute(), {});
  assert.deepEqual(await execute({ metadata: { ofi_tenant_id: "tenant-17" } }), {});
});

test("omits invalid roles and never fabricates a default role", async () => {
  for (const role of ["", "Farmer", "admin", "platform_admin", "system ", 12, null]) {
    const claims = await execute({
      metadata: { ofi_role: role, ofi_tenant_id: "tenant-17" }
    });
    assert.equal(Object.hasOwn(claims, "https://claims.example.org/ofi/role"), false);
    assert.equal(Object.hasOwn(claims, "https://claims.example.org/ofi/tenant_id"), false);
  }
});

test("omits all claims for missing or invalid namespaces", async () => {
  for (const namespace of [
    undefined,
    "",
    "http://claims.example.org/ofi/",
    "https://claims.example.org/ofi",
    "https://claims.example.org/ofi/?x=1",
    "https://claims.example.org/ofi/#fragment",
    "https://user:password@claims.example.org/ofi/",
    "not a URL"
  ]) {
    const claims = await execute({
      namespace,
      metadata: { ofi_role: "farmer", ofi_tenant_id: "tenant-17" }
    });
    assert.deepEqual(claims, {}, `namespace should be rejected: ${String(namespace)}`);
  }
});

test("emits only the role when the tenant ID is invalid", async () => {
  for (const tenantId of ["", "   ", 7, null, "x".repeat(513)]) {
    const claims = await execute({
      metadata: { ofi_role: "farmer", ofi_tenant_id: tenantId }
    });
    assert.deepEqual(claims, {
      "https://claims.example.org/ofi/role": "farmer"
    });
  }
});

test("does not read user-editable metadata, email, or phone for authorization", async () => {
  const claims = await execute({ metadata: undefined });
  assert.deepEqual(claims, {});
});
