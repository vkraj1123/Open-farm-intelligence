# Staging invitation acceptance check

This check is designed to be launched from GitHub on a phone. It performs a real, short-lived invitation operation against a deployment you explicitly identify as **staging**; it does not deploy OFI or configure an identity/email provider.

## What it verifies

The workflow checks the staging health and readiness endpoints, uses a staging tenant-administrator bearer token to create a one-hour invitation for a configured test identity-provider subject, verifies no-store response headers, verifies the webhook-mode response contains no token/secret fields, confirms the API reports webhook acceptance with a well-formed event ID, and checks the invitation is visible in the durable metadata list.

It intentionally does **not** print request/response bodies, the admin bearer token, the invited subject, or the invitation token. Redirects are rejected so the bearer token cannot be forwarded to another host. TLS certificate verification remains enabled. The configured origin must be HTTPS and must not be a local/private IP target.

A passing workflow proves the staging API accepted the delivery handoff and persisted the invitation metadata. It does not prove that a human received the message, that the delivery bridge mapped the subject to the intended verified address, or that redemption succeeded. Those remain explicit acceptance steps.

## One-time setup in GitHub (Android browser/app)

1. Open the repository on GitHub, go to **Settings → Environments**, and create an environment named \`ofi-staging\`. Restrict deployments to \`main\`. Add required reviewers/protection where your repository plan supports it; the workflow also requires a typed confirmation.
2. In the \`ofi-staging\` environment, add this variable:
   - \`OFI_STAGING_BASE_URL\` — the root origin of the deployed staging API, for example \`https://ofi-staging.example.net\`. Do not include a path, query, fragment, or credentials.
3. Add these **environment secrets**:
   - \`OFI_STAGING_ADMIN_BEARER_TOKEN\` — a short-lived staging tenant-admin access token for the selected tenant.
   - \`OFI_STAGING_TENANT_ID\` — the exact staging tenant identifier.
   - \`OFI_STAGING_INVITED_SUBJECT_ID\` — the stable \`sub\` value of a disposable staging identity that can receive the invitation.
4. Confirm that the deployment is configured for signed webhook delivery and durable storage. It needs \`OFI_INVITATION_DELIVERY_MODE=webhook\`, the trusted delivery bridge URL and HMAC secret, PostgreSQL-backed invitations, verified TLS, and required tenant-membership enforcement. Keep the HMAC secret in the deployment secret manager, separate from the GitHub staging admin token.
5. Verify that the configured test subject does not already have a pending invitation. Use a disposable staging account, not a real farmer or production identity.

Do not store credentials in GitHub Actions inputs, source code, README files, issue comments, or chat. Do not use a public request-bin/webhook inspector as the bridge: the event body contains a usable one-time credential.

## Launch from GitHub

1. Open **Actions → Staging invitation acceptance**.
2. Select **Run workflow** on branch \`main\`.
3. Enter exactly \`RUN-STAGING-INVITATION-CHECK\` in the confirmation field. This creates an invitation valid for one hour and can trigger a real staging notification.
4. Open the run and read the final step. A pass prints only a safe summary and the delivery event ID; use that ID to correlate the bridge's **redacted** receipt/operations view.

The script uses no third-party Python packages. It is part of the normal test suite so unsafe URL and token-response regressions run in regular CI too.

## Complete the real-provider acceptance

After the workflow passes:

- In the bridge/provider's protected operations view, verify the event ID was accepted once and that its signature was checked against the exact raw body. Confirm tokens are redacted from application, proxy, tracing and email-provider logs.
- Confirm the message reaches only the intended test identity's verified delivery channel. Do not copy the token into Actions logs or support notes.
- Authenticate as the exact target identity through the staging identity provider. Redeem using that identity and the delivered token against the same tenant. Verify the new membership is active and has only the regular \`member\` role.
- Verify a second redemption is rejected, a different subject cannot redeem the invitation, and a different tenant path cannot redeem it.
- For a separate, deliberate negative-test deployment/window, test bridge rejection (bad signature, unknown version, duplicate event, redirect/non-2xx and timeout). Confirm failed or ambiguous OFI delivery invalidates the invitation; do not use production for these tests.

## Failure handling

The API revokes an invitation when the bridge returns a non-2xx status, redirects, or produces an ambiguous transport outcome, but the runner can itself lose contact after the API has processed a request. If the workflow reports an ambiguous creation failure, inspect the staging invitation list before rerunning. The script attempts best-effort cleanup when it can identify an invitation created by that run. If cleanup cannot be confirmed, an operator must inspect and revoke any unintended pending invitation.

A green GitHub run is a useful staging checkpoint, **not a production approval**. Production rollout still requires live-provider testing, correct verified-subject mapping, email/notification controls, secret rotation, rate limiting, monitoring, operational ownership and independent security review.
