# Authenticated consent API

The consent API accepts only a verified bearer token from a trusted identity
issuer. It does not accept an actor ID from the request body as evidence of
identity. Configure these deployment environment variables:

- `OFI_AUTH_HMAC_SECRET`: a high-entropy secret of at least 32 UTF-8 bytes,
  shared only with the trusted token issuer and OFI.
- `OFI_AUTH_ISSUER`: the exact expected JWT `iss` claim.
- `OFI_AUTH_AUDIENCE`: the exact JWT audience accepted by OFI.

The trusted issuer must sign compact JWTs with HS256 and include `sub`
(actor ID), `role`, `tenant_id`, `iss`, `aud`, `iat`, and `exp`.
The issuer must authorize the actor's membership in the named tenant before
minting the token. The verifier rejects
other algorithms, invalid signatures, expired/future-issued tokens, tokens
with the wrong issuer/audience, unsupported roles, and tokens whose lifetime
exceeds 24 hours. OFI validates tokens but does not issue them; use a trusted
issuer and keep the shared secret in a secret manager.

Consent routes are:

- `POST /consents`: create a grant only when the authenticated actor is a
  farmer, cultivator, or owner and the request explicitly sets
  `affirmative: true`, a purpose, and one or more unique service scopes.
- `GET /consents/{consent_id}`: retrieve only the grant owned by the
  authenticated actor.
- `DELETE /consents/{consent_id}`: record the first revocation timestamp for
  a grant owned by the authenticated actor. The database record is retained.

Without auth configuration, these routes fail closed with HTTP 503. Missing or
invalid credentials receive HTTP 401; a role that cannot issue/revoke consent
receives HTTP 403; a missing or another actor's grant is reported as HTTP 404.
Every consent request must carry the signed `tenant_id` claim; the tenant is
not accepted from the request body. Grant records are process-local when
`OFI_DATABASE_URL` is unset; production must use PostgreSQL with migration v4
applied.

## Resource access and remaining limits

The consent API is part of a broader tenant-aware JWT boundary for case/farm,
evidence, evidence-assessment and policy routes. Those routes require a signed
non-empty `tenant_id` claim by default, except public `/health` and `/ready`
endpoints. A farm is accessible only within the token's tenant and by its
primary farmer or a currently valid land party with `verification: "verified"`
and a `role` matching `owner` or `cultivator`. Policy versions are separately
namespaced by tenant; consent and assessment lookups are tenant-filtered.
Cross-tenant farm/case/consent lookups return 404. Global evidence-comparison
policy writes are limited to `agent`, `service_provider`, and `system` roles.

The resource guard can be disabled only with the explicit
`OFI_RESOURCE_AUTH_ENABLED=false` setting, intended solely for isolated unit
tests or a trusted local sandbox. It defaults to enabled; never disable it on
a reachable deployment. Missing JWT configuration fails closed with HTTP 503.

Migration v4 adds tenant columns and storage scoping to farms, cases, consent
grants, evidence policy versions and assessments. Existing rows are backfilled
to `legacy:default`; that label is not an organization and must not be granted
to ordinary users. Existing records need an approved, audited ownership mapping
before multi-tenant production cutover. Tenant membership is not managed inside
OFI yet—the trusted token issuer must validate membership before signing the
`tenant_id` claim. OFI still lacks a tenant membership registry/lifecycle,
complete RBAC, downstream purpose limitation, audit monitoring, rate limiting,
TLS enforcement, automated key rotation, and an independent security review.
OFI validates HS256 tokens but does not issue them; keep the shared secret in a
deployment secret manager. A farmer-facing consent UI remains open.
