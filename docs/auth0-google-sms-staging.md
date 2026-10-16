# Google sign-in + phone-number OTP for OFI staging (Auth0)

This runbook adds two sign-in choices through one trusted identity broker:

1. **Continue with Google** (Auth0 Google social connection).
2. **Continue with phone** (Auth0 passwordless SMS one-time code).

Both methods must authenticate a person through Auth0 first. OFI then accepts only an RS256 access token issued by the configured Auth0 issuer for the OFI API audience, with authorization claims supplied by a trusted authorization source. Phone possession and Google sign-in are identity checks; neither automatically grants an OFI role or tenant membership.

This is an integration guide, not a claim that Auth0 has been configured. Configuration must be performed by the account owner in the Auth0 dashboard, Google developer console, and SMS messaging provider. No secrets are required in this repository.

## 1. Create or choose a dedicated staging Auth0 tenant

- Use a dedicated development/staging tenant, not the production tenant.
- Register the eventual Android/web client separately from any production application.
- Use Auth0 Universal Login or a maintained Auth0 SDK; do not build your own passwordless verification UI or store OTPs.
- Set the application's allowed callback/logout/web-origin URLs only to the actual app origins you control. Do not use the Render API URL as a login callback unless a real client on that origin handles Auth0 callbacks.
- Store client secrets in the relevant provider dashboard/secret manager. Never put them in GitHub workflow inputs, the README, a source file, or a chat.

## 2. Enable Google sign-in

In Auth0 Dashboard, navigate to **Authentication → Social** and enable/create the Google connection.

- For initial development, Auth0 may offer development keys, but use a Google OAuth client owned by the project before a shared or production-like rollout.
- Add the Auth0 callback URL exactly as shown by the Auth0 application in the Google OAuth client configuration.
- Enable the Google connection for the staging application.
- Test the connection through Auth0's own connection test.

Google provides a verified identity to Auth0. Do not assume that a Google ID token is automatically an OFI API access token.

Official guide: [Auth0 — Add Google Login](https://auth0.com/docs/authenticate/identity-providers/social-identity-providers/google).

## 3. Enable phone-number login with SMS OTP

In Auth0 Dashboard, configure a **Passwordless → SMS** connection and enable it for the same staging application that uses Google sign-in.

- Configure an approved phone messaging/SMS provider in Auth0 (for example, a project-owned Twilio account where supported), including provider credentials in the Auth0 dashboard/secret manager.
- Review provider country coverage, sender requirements, delivery rates, spending limits, and any account or regional verification requirements before live testing.
- Use international E.164 input format, e.g. `+<country-code><number>`; do not hard-code a real person's phone number in tests or docs.
- Request a short one-time verification code through Auth0's supported passwordless flow. Verify it through Auth0's supported SDK/API flow; never issue or validate the OTP inside OFI itself.
- Test with a phone number you control and a disposable staging identity. Configure rate limits/anti-abuse controls and monitor SMS spend to prevent OTP bombing and unexpected charges.
- Do not expose OTPs, verification links, access tokens, refresh tokens, or phone provider credentials in logs or analytics.

Official guides: [Auth0 — Send a code by SMS](https://auth0.com/docs/api/authentication/passwordless/get-code-or-link), [Auth0 — Authenticate with a passwordless verification code](https://auth0.com/docs/api/authentication/passwordless/authenticate-user), and [Configure phone messaging providers](https://auth0.com/docs/customize/phone-messages/configure-phone-messaging-providers).

## 4. Register the OFI API in Auth0

In Auth0 Dashboard, create a custom API for OFI and select **RS256** as its signing algorithm. Define a stable API Identifier/audience for this environment, and use the exact same value in the OFI API's `OFI_AUTH_AUDIENCE` environment variable.

The access-token request must target that API audience. A token issued for Auth0's Management API or only for the user-info endpoint is not an OFI API access token.

After obtaining the Auth0 issuer and the JWKS URI from that tenant's own OIDC discovery document, run the [OIDC provider metadata preflight](https://github.com/vkraj1123/Open-farm-intelligence/actions/workflows/oidc-provider-preflight.yml) from GitHub on your phone. Paste the exact values from the discovery document. Do not guess Auth0's tenant domain, issuer, or JWKS URL.

Official guide: [Auth0 — Get Access Tokens](https://auth0.com/docs/secure/tokens/access-tokens/get-access-tokens).

## 5. Configure trusted authorization claims before enabling OIDC in Render

OFI's current verifier requires:

- a valid RS256 access-token signature;
- exact `iss` and `aud` matches;
- subject and time claims that pass OFI's lifetime rules;
- one allowed OFI role claim (`farmer`, `cultivator`, `owner`, `agent`, `service_provider`, or `system`);
- and, for tenant-scoped routes, the matching tenant claim plus an active authoritative membership record in OFI.

Neither a Google connection nor an SMS connection supplies the OFI role and tenant authorization model by itself. **Do not set `role=owner`, `role=system`, or tenant claims from the phone number, email address, Google Workspace domain, or arbitrary user-editable profile metadata.** Use an explicitly trusted authorization source and a reviewed Auth0 Post-Login Action (or dedicated token broker) to map already-authorized membership/roles. Keep tenant-membership enforcement required.

Auth0 custom claims should use a collision-resistant namespace. OFI supports configurable claim names via `OFI_AUTH_ROLE_CLAIM` and `OFI_AUTH_TENANT_CLAIM`; if using namespaced keys, set these environment variables to the exact claim names and test them. The namespaced phone claims described below are profile/verification signals, not authorization claims.

Do not change the deployed Render service from `OFI_AUTH_MODE=hs256` to `oidc_jwks` until the exact Auth0 issuer, JWKS URL, audience and trusted authorization-claim path are configured and a real staging access token is tested end to end. Keep the current auth mode until that checklist is complete.

## 6. Optional verified phone claims

Auth0's user profile uses `phone_number` and `phone_verified` for the SMS identity. If the client genuinely needs the phone number in the access token, use a Post-Login Action that copies the provider's verified phone status into a namespaced claim. Use a namespace under a domain you control; do not use the sample placeholder unchanged.

Example Action body (replace the namespace before publishing):

```javascript
exports.onExecutePostLogin = async (event, api) => {
  const namespace = "https://YOUR-DOMAIN-YOU-CONTROL/claims/";

  // Replace this placeholder with a namespace you control before publishing.
  if (namespace.includes("YOUR-DOMAIN-YOU-CONTROL")) {
    api.access.deny("OFI claim namespace must be configured");
    return;
  }

  const phone = event.user && event.user.phone_number;
  const verified = event.user && event.user.phone_verified === true;

  if (typeof phone === "string" && phone.length > 0 && verified) {
    api.accessToken.setCustomClaim(namespace + "phone_number", phone);
    api.accessToken.setCustomClaim(namespace + "phone_number_verified", true);
  }
};
```

This Action intentionally omits phone claims when the provider has not verified the phone number. Do not copy an unverified phone number as verified. Do not use these claims as tenant authorization, and do not assume Google sign-in always returns a verified phone.

## 7. Staging acceptance checklist

- [ ] Google connection is enabled and tested for the staging application.
- [ ] SMS/passwordless connection is enabled and tested with a phone number you control.
- [ ] SMS delivery provider credentials and spending controls are configured in Auth0/provider settings.
- [ ] A custom OFI API audience is registered with RS256 signing.
- [ ] The issuer/JWKS metadata preflight passes for the actual Auth0 tenant.
- [ ] A real access token for the OFI API audience has the expected issuer, audience, subject, expiry, role claim, and trusted tenant claim when needed.
- [ ] A user cannot assign themselves an OFI role or tenant by editing their profile or using a different phone number.
- [ ] A revoked/suspended membership is denied by OFI's authoritative membership registry.
- [ ] SMS OTP and token values do not appear in app, API, provider, proxy, or GitHub logs.
- [ ] Staging invitation delivery is tested separately with a trusted signed delivery bridge; login success alone does not verify invitations or redemption.

Do not use real farmer records or production accounts on the temporary Free-tier staging deployment. Do not treat this runbook or a metadata-only workflow pass as production approval.
