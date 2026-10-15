# Tenant invitation delivery webhook

This runbook configures OFI to hand invitation tokens to a deployment-owned
delivery bridge. OFI does not send mail directly and does not decide which email
address belongs to a subject. The bridge must resolve the immutable identity
provider subject through a trusted directory, then deliver the token through an
approved channel.

## Deployment configuration

Keep these settings in your deployment configuration/secret manager. Do not
commit the secret or paste it into logs.

| Variable | Required in webhook mode | Meaning |
|---|---|---|
| \`OFI_INVITATION_DELIVERY_MODE\` | Yes | Set to \`webhook\`; defaults to \`manual\` |
| \`OFI_INVITATION_DELIVERY_WEBHOOK_URL\` | Yes | Absolute HTTPS URL of the trusted bridge; URL credentials, query strings, fragments and redirects are rejected |
| \`OFI_INVITATION_DELIVERY_WEBHOOK_SECRET\` | Yes | HMAC key of at least 32 UTF-8 bytes; provision and rotate via a secret manager |
| \`OFI_INVITATION_DELIVERY_TIMEOUT_SECONDS\` | No | Transport timeout; defaults to 5 seconds and must be greater than 0 and at most 30 |
| \`OFI_DATABASE_URL\` | Yes for durable production use | PostgreSQL DSN; webhook mode refuses the process-local in-memory membership/invitation store |
| \`OFI_TENANT_MEMBERSHIP_ENFORCEMENT\` | Yes | Keep at \`required\` outside test environments |
| \`OFI_AUTH_MODE\` | For OIDC deployments | Configure using [the identity-provider runbook](identity-provider.md) |

If delivery mode is omitted or set to \`manual\`, the legacy manual flow stays
available: the API returns the token once in a \`Cache-Control: no-store\`
response. Any other delivery mode fails closed. In \`webhook\` mode, the API does
not return the token to the API caller. The token is sent only to the configured
bridge over verified HTTPS.

## Outbound event contract

OFI sends one HTTPS POST with \`Content-Type: application/json\`. The receiver
must verify the HMAC over the **exact raw request body bytes** before parsing or
acting on the payload.

Headers:

- \`X-OFI-Event: ofi.tenant_invitation.created\`
- \`X-OFI-Event-ID: <SHA-256 hex digest of the exact body>\`
- \`X-OFI-Signature: sha256=<HMAC-SHA256 hex digest>\`

The versioned JSON body contains only the following fields:

| Field | Meaning |
|---|---|
| \`event\` | \`ofi.tenant_invitation.created\` |
| \`version\` | Contract version, currently \`1\` |
| \`invitation_id\` | Immutable OFI invitation identifier; use as an audit/deduplication aid |
| \`tenant_id\` | Intended tenant |
| \`subject_id\` | Expected stable identity-provider \`sub\` value |
| \`invitation_token\` | One-time secret; never write it to logs, tracing, metrics or durable queues unless those are specifically designed as secret stores |
| \`expires_at\` | ISO-8601 timestamp including timezone |
| \`redemption_path\` | Relative OFI API path to submit the token after the target user is authenticated |

The receiver should:
1. Verify the signature in constant time against the raw body and a key held in a secret manager.
2. Reject unknown event types/versions, expired invitations, malformed values and unsigned requests.
3. Resolve \`subject_id\` against a trusted identity directory; do not trust a caller-supplied email or use an email address as a substitute for the OIDC subject.
4. Deliver the token only to the verified delivery address, over the receiver's approved channel. Prevent token leakage in proxy logs, analytics, support tickets and email-provider diagnostics.
5. Deduplicate on \`X-OFI-Event-ID\` or the invitation ID; persist only the minimum delivery receipt and avoid persisting the token.
6. Return a 2xx response only after it has accepted responsibility for this event. Return a non-2xx on rejection.

## Failure and retry semantics

OFI makes a single request and deliberately does not retry. A redirect, non-2xx status or ambiguous transport failure is treated as failed delivery. OFI then tries to revoke the invitation so an unknown delivery outcome cannot leave a token active. If revocation cannot be confirmed, the API returns a sanitized 503 and an operator must inspect the invitation through the tenant invitation administration endpoints. Create a new invitation for a retry rather than replaying an old token.

The request timeout bounds the API's wait; it is not a guarantee that a remote receiver did not process a timed-out request. The receiver must also avoid unintended duplication. Do not use an arbitrary request-bin, public webhook tester, or third-party URL as the delivery endpoint: the token is a credential.

## Rollout checklist

- Deploy and test the bridge in a staging environment with a separate HMAC secret.
- Verify signature checks using the raw request body, event deduplication, subject-to-address mapping and email/token redaction.
- Verify invitation creation returns metadata and a delivery event ID, but no raw token.
- Simulate 400/500 responses, redirects and timeouts; confirm the invitation becomes revoked and a fresh invitation is required.
- Verify the bridge delivers only to the intended subject's verified channel and that redemption requires a matching authenticated OIDC subject.
- Configure TLS certificate validation, outbound egress restrictions, access logging with body/secret redaction, secret rotation, alerting and a named operational owner.
- Keep real-provider end-to-end testing and independent security review as release gates.

The checked-in tests use a mocked transport. They verify payload signing, endpoint validation, timeout handling, sanitized errors and fail-closed API behavior, but do not prove connectivity to or operation of a real delivery provider.
