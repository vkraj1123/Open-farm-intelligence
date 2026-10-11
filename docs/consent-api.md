# Authenticated consent API

The consent API accepts only a verified bearer token from a trusted identity
issuer. It does not accept an actor ID from the request body as evidence of
identity. Configure these deployment environment variables:

- \`OFI_AUTH_HMAC_SECRET\`: a high-entropy secret of at least 32 UTF-8 bytes,
  shared only with the trusted token issuer and OFI.
- \`OFI_AUTH_ISSUER\`: the exact expected JWT \`iss\` claim.
- \`OFI_AUTH_AUDIENCE\`: the exact JWT audience accepted by OFI.

The trusted issuer must sign compact JWTs with HS256 and include \`sub\`
(actor ID), \`role\`, \`iss\`, \`aud\`, \`iat\`, and \`exp\`. The verifier rejects
other algorithms, invalid signatures, expired/future-issued tokens, tokens
with the wrong issuer/audience, unsupported roles, and tokens whose lifetime
exceeds 24 hours. OFI validates tokens but does not issue them; use a trusted
issuer and keep the shared secret in a secret manager.

Consent routes are:

- \`POST /consents\`: create a grant only when the authenticated actor is a
  farmer, cultivator, or owner and the request explicitly sets
  \`affirmative: true\`, a purpose, and one or more unique service scopes.
- \`GET /consents/{consent_id}\`: retrieve only the grant owned by the
  authenticated actor.
- \`DELETE /consents/{consent_id}\`: record the first revocation timestamp for
  a grant owned by the authenticated actor. The database record is retained.

Without auth configuration, these routes fail closed with HTTP 503. Missing or
invalid credentials receive HTTP 401; a role that cannot issue/revoke consent
receives HTTP 403; a missing or another actor's grant is reported as HTTP 404.
Grant records are process-local when \`OFI_DATABASE_URL\` is unset; production
must use PostgreSQL with migration v3 applied.

## Important limits

This is the authenticated **consent lifecycle** increment, not complete API
security. It does not yet protect the existing case, farm, evidence, or
assessment routes; does not provide tenant isolation or RBAC across those
resources; and does not replace a deployment-wide identity provider, key
rotation policy, rate limiting, TLS enforcement, audit monitoring, or a
security review. Do not expose the wider API to untrusted networks until those
boundaries are in place. Deployments should prefer an established OIDC/JWT
verification gateway and may adapt the verifier to that trusted boundary.
