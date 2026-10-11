# Authenticated consent API

The consent API accepts only a verified bearer token from a trusted identity
issuer. It does not accept an actor ID from the request body as evidence of
identity. Configure these deployment environment variables:

- `OFI_AUTH_HMAC_SECRET`: a high-entropy secret of at least 32 UTF-8 bytes,
  shared only with the trusted token issuer and OFI.
- `OFI_AUTH_ISSUER`: the exact expected JWT `iss` claim.
- `OFI_AUTH_AUDIENCE`: the exact JWT audience accepted by OFI.

The trusted issuer must sign compact JWTs with HS256 and include `sub`
(actor ID), `role`, `iss`, `aud`, `iat`, and `exp`. The verifier rejects
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
Grant records are process-local when `OFI_DATABASE_URL` is unset; production
must use PostgreSQL with migration v3 applied.

## Resource access and remaining limits

The consent API is part of a broader JWT boundary for case/farm/evidence and
evidence-assessment routes. Those routes require authentication by default,
except public `/health` and `/ready` endpoints. The signed actor may access a
farm as its primary `farmer_id`, or as a currently valid land party with
`verification: "verified"` and a `role` matching `owner` or `cultivator`.
Cross-actor farm/case lookups return 404. Global evidence-comparison policy
writes are limited to `agent`, `service_provider`, and `system` roles.

The resource guard can be disabled only with the explicit
`OFI_RESOURCE_AUTH_ENABLED=false` setting, intended solely for isolated unit
tests or a trusted local sandbox. It defaults to enabled; never disable it on
a reachable deployment. Missing JWT configuration fails closed with HTTP 503.

This is still not complete multi-tenancy or full API security. It does not yet
define organization/tenant IDs or membership, complete RBAC, purpose limitation
for downstream data use, audit monitoring, rate limiting, TLS enforcement, key
rotation automation, or a security review. OFI validates HS256 tokens but does
not issue them; connect a trusted issuer and protect the shared secret in a
deployment secret manager. A farmer-facing consent UI and production issuer
integration remain deployment/product work.
