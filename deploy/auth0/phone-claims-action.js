/**
 * Optional Auth0 Post-Login Action: expose an already-verified phone number
 * as namespaced access-token claims.
 *
 * In the Auth0 Action, define secret OFI_CLAIM_NAMESPACE as a namespace under
 * a domain your project controls, ending in "/". This is not an SMS/provider
 * credential. If omitted or invalid, login continues without these optional
 * claims; authorization must never depend on them.
 *
 * This Action intentionally does not assign OFI roles or tenant membership.
 */
exports.onExecutePostLogin = async (event, api) => {
  const namespace = event.secrets && event.secrets.OFI_CLAIM_NAMESPACE;

  if (
    typeof namespace !== "string" ||
    !namespace.startsWith("https://") ||
    !namespace.endsWith("/") ||
    namespace.includes("?") ||
    namespace.includes("#")
  ) {
    // Phone-profile claims are optional; do not break sign-in if not configured.
    return;
  }

  const user = event.user || {};
  const phone = user.phone_number;
  const phoneVerified = user.phone_verified === true;

  // Auth0's profile flag is phone_verified; publish the claim only on true.
  // A Google login with no verified phone will therefore omit these claims.
  if (phoneVerified && typeof phone === "string" && phone.trim().length > 0) {
    api.accessToken.setCustomClaim(namespace + "phone_number", phone);
    api.accessToken.setCustomClaim(namespace + "phone_number_verified", true);
  }
};
