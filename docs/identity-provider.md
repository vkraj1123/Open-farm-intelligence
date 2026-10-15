# Identity-provider integration: OIDC/JWKS bearer verification

OFI can validate bearer JWTs signed by an external OIDC identity provider. This
adds a token-verification adapter; it does **not** issue tokens, create provider
accounts, verify email addresses, implement SSO/MFA itself, or prove tenant
membership. The OFI membership registry remains authoritative for access to a
tenant, and the issuer must only emit OFI role and tenant claims from trusted
identity/authorization data.

## Modes

- `OFI_AUTH_MODE=hs256` (default): preserves the existing compact HS256 token
  profile and requires `OFI_AUTH_HMAC_SECRET` (at least 32 UTF-8 bytes),
  `OFI_AUTH_ISSUER`, and `OFI_AUTH_AUDIENCE`.
- `OFI_AUTH_MODE=oidc_jwks`: verifies RS256 tokens using a configured HTTPS
  JWKS endpoint. Install the runtime extra with `python -m pip install -e
  ".[auth]"`. Do not configure an HMAC secret as a substitute for an OIDC
  issuer's public keys.

For OIDC/JWKS mode, configure these deployment variables through your secret
manager/configuration system:

| Variable | Required | Meaning |
|---|---|---|
| `OFI_AUTH_JWKS_URL` | Yes | Absolute HTTPS URL for the trusted issuer's JWKS endpoint; URL credentials, query strings and fragments are rejected |
| `OFI_AUTH_ISSUER` | Yes | Exact issuer value expected in `iss` |
| `OFI_AUTH_AUDIENCE` | Yes | API audience expected in `aud` |
| `OFI_AUTH_ROLE_CLAIM` | No | Claim name that contains one OFI role; defaults to `role` |
| `OFI_AUTH_TENANT_CLAIM` | No | Claim name that contains the tenant ID; defaults to `tenant_id` |

The configured role claim must be a single string in OFI's allow-list:
`farmer`, `cultivator`, `owner`, `agent`, `service_provider`, or
`system`. Map external groups/roles to this claim at the identity provider or
trusted token broker; OFI does not interpret an arbitrary group list as an
administrator role. The tenant claim, when present, must be a non-empty string
no longer than 512 characters. Routes that use tenant scope still require the
claim and, independently, a current active record in OFI's membership registry.
Never assign `system` to ordinary users.

## Validation and failure behavior

OIDC/JWKS mode:

- accepts **RS256 only** and requires a non-empty `kid`; token-controlled
  algorithm negotiation is not permitted;
- validates signature, exact issuer, audience, expiry, not-before, subject,
  issued-at and the 24-hour maximum lifetime;
- permits at most 60 seconds of future skew for `iat`; expiry/not-before do not
  receive a positive leeway;
- caches the JWKS set in process for up to five minutes, refreshing when a
  token references an unknown key ID;
- limits bearer tokens to 16 KiB and returns a sanitized 401 for invalid
  credentials; a key-endpoint connection failure returns a sanitized 503 so
  an infrastructure outage is not confused with a bad credential;
- fails closed on missing/malformed configuration and unknown auth modes.

JWKS is fetched over HTTPS by the API process. Ensure network egress, DNS,
certificate validation and outbound proxy policy are configured for the
identity provider. Keep the provider's issuer, audience and JWKS URL under
deployment control—never accept them from a request.

## Migration path and limitations

This feature is opt-in to avoid changing existing deployments. Test a
staging-only service client with the identity provider before changing
`OFI_AUTH_MODE` in any deployed environment. Ensure the external JWT contains
OFI-specific role/tenant claims from trusted authorization data, then verify
suspended/revoked membership still blocks tenant-scoped requests. Keep
`OFI_TENANT_MEMBERSHIP_ENFORCEMENT` at `required` outside tests.

The current implementation supports one trusted issuer/JWKS endpoint per API
process configuration. It is not an OIDC login flow or authorization-code
client, and does not implement group synchronization, email verification, MFA
policy, token issuance, or key-management operations. Tenant invitations and an
opt-in signed HTTPS delivery webhook are implemented as a separate OFI boundary;
the webhook does not replace provider identity proofing or membership claims.
See [the invitation delivery runbook](tenant-invitation-delivery.md). Neither
synthetic RSA tests nor mocked delivery tests substitute for staging tests with
the selected identity provider and delivery receiver, or an independent
production security assessment.
