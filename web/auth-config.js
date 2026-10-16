/*
 * Public browser configuration for the OFI staging login client.
 *
 * These values are intentionally public and are not secrets. Never put an
 * Auth0 client secret, SMS provider credential, or API bearer token here.
 * Replace domain/clientId only after creating an Auth0 Single Page Application.
 */
window.OFI_AUTH_CONFIG = Object.freeze({
  // Auth0 tenant hostname only, e.g. "your-tenant.eu.auth0.com" (no https://).
  domain: "",
  // Auth0 Single Page Application client ID (public, not a client secret).
  clientId: "",
  // Must exactly match the Identifier of the Auth0 API registered for OFI.
  // The current OFI staging API is configured with this audience.
  audience: "ofi-api",
  // Informational link only; this page does not call protected API routes yet.
  apiBaseUrl: "https://ofi-staging-api.onrender.com",
  // Default Auth0 connection names. Change only if your tenant uses different names.
  googleConnection: "google-oauth2",
  smsConnection: "sms"
});
