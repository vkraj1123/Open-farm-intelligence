/**
 * OFI Auth0 Post-Login Action template for trusted role/tenant claims.
 *
 * Required Action secret:
 *   OFI_CLAIM_NAMESPACE=https://<namespace-under-a-domain-you-control>/
 *
 * Before enabling this Action, establish an administrator-controlled process
 * to write event.user.app_metadata.ofi_role and optionally
 * event.user.app_metadata.ofi_tenant_id only after verifying OFI authorization.
 *
 * Never populate these app_metadata values from browser requests, user_metadata,
 * a phone number, an email address, a Google Workspace domain, or an untrusted
 * group/role claim. Auth0's app_metadata is expected to be writable only by
 * trusted administrator/onboarding tooling, not by the end user.
 *
 * If the namespace or role is absent/invalid, the Action omits the role claim.
 * OFI's API verifier requires a recognized role and will reject a token missing
 * it. A tenant claim is emitted only when valid; OFI still checks its own active
 * membership registry for every tenant-scoped request.
 *
 * In Render, the eventual OIDC/JWKS configuration must set:
 *   OFI_AUTH_ROLE_CLAIM=<OFI_CLAIM_NAMESPACE value> + "role"
 *   OFI_AUTH_TENANT_CLAIM=<OFI_CLAIM_NAMESPACE value> + "tenant_id"
 * Keep the API in HS256 mode until the full acceptance checklist passes.
 */

const ALLOWED_OFI_ROLES = new Set([
  "farmer",
  "cultivator",
  "owner",
  "agent",
  "service_provider",
  "system"
]);

function validClaimNamespace(value) {
  if (
    typeof value !== "string" ||
    !value.startsWith("https://") ||
    !value.endsWith("/") ||
    value.includes("?") ||
    value.includes("#")
  ) {
    return false;
  }

  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      Boolean(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    );
  } catch (_) {
    return false;
  }
}

function validTenantId(value) {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.trim().length <= 512
  );
}

exports.onExecutePostLogin = async (event, api) => {
  const secrets = event.secrets || {};
  const namespace = secrets.OFI_CLAIM_NAMESPACE;

  if (!validClaimNamespace(namespace)) {
    // Do not invent a namespace or emit unnamespaced authorization claims.
    return;
  }

  const user = event.user || {};
  const metadata = user.app_metadata || {};
  const role = metadata.ofi_role;

  // No default role. If no trusted, recognized role has been assigned, the
  // downstream OFI verifier must reject the access token for OFI API use.
  if (typeof role !== "string" || !ALLOWED_OFI_ROLES.has(role)) {
    return;
  }

  api.accessToken.setCustomClaim(namespace + "role", role);

  const tenantId = metadata.ofi_tenant_id;
  if (validTenantId(tenantId)) {
    api.accessToken.setCustomClaim(namespace + "tenant_id", tenantId.trim());
  }
};
