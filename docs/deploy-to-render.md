# Phone-first Render staging deployment

This is the first deployment route for testing OFI's FastAPI service without a
desktop. It is **temporary staging infrastructure**, not a production
recommendation.

## Before you begin

- The repository is public: [vkraj1123/Open-farm-intelligence](https://github.com/vkraj1123/Open-farm-intelligence).
- Sign in to [Render](https://render.com/) with a browser on your phone.
- Approve Render's request to access the GitHub repository when prompted.
- Do not enter production credentials or real farmer/tenant records in this
  temporary environment.

## One-tap Blueprint setup

After the deployment files are merged into `main`, open:

[Deploy OFI staging to Render](https://render.com/deploy?repo=https://github.com/vkraj1123/Open-farm-intelligence)

Review the Blueprint before approving it. It creates:

- `ofi-staging-api`: Python/FastAPI web service in Singapore, Free plan.
- `ofi-staging-web`: global static site hosting the phone-first Google/SMS sign-in client.
- `ofi-staging-db`: PostgreSQL 16 in Singapore, Free plan, restricted to
  Render's private network.
- Generated HMAC secret for the API's existing HS256 verifier.
- The ordered database migrations run through `ofi-schema` at startup before
  Uvicorn starts.

If Render asks you to connect GitHub, select this repository and authorize only
the repository access you intend to grant. Approve the Blueprint after checking
the service names and Free plans. If Render reports a Blueprint YAML error,
stop and copy the error text; do not create a different service manually yet.

## Important Free-plan limitation

Render documents that Free Postgres databases expire **30 days after creation**
and are deleted after the additional 14-day upgrade grace period. The Free web
service can spin down after inactivity and has no persistent local disk.
Consequently, this configuration is for an initial smoke test only. Do not use
it as the system of record, for real farmer data, or for production. Before
longer-lived staging, arrange a database plan with suitable retention/backups
and review hosting costs and operational controls.

## Verify the live API

In the Render dashboard, wait for the database and web service to finish
deploying. Open the service logs and confirm that `ofi-schema` finishes with a
current schema version and Uvicorn starts without errors.

Then open these URLs using the actual hostname shown in Render:

- `https://<your-service>.onrender.com/health` — expected HTTP 200 and
  `{"status":"ok"}`.
- `https://<your-service>.onrender.com/ready` — expected HTTP 200,
  `status: "ready"`, `persistence: "postgresql"`, and `durable: true`.

The readiness endpoint also checks PostGIS and the complete current migration
schema. Do not continue to invitation testing until both checks pass. The health
route can pass while database readiness fails.

After you know the real service URL, update GitHub's
`ofi-staging` environment variable `OFI_STAGING_BASE_URL` to the origin only,
for example `https://ofi-staging-api.onrender.com`. Do not include a slash path,
query, fragment, whitespace, or a trailing backslash.

## Verify the API from GitHub on your phone

After Render creates the service, open the [Staging API readiness workflow](https://github.com/vkraj1123/Open-farm-intelligence/actions/workflows/staging-api-readiness.yml) while signed in to GitHub. Select **Run workflow**, choose the `main` branch, and paste the actual root origin Render assigned, such as `https://ofi-staging-api.onrender.com`.

This read-only workflow checks `/health` and `/ready` over verified HTTPS. It confirms the API is alive and that PostgreSQL, PostGIS, durable persistence, and the complete current migration schema are ready. It rejects placeholder or non-Render URLs, does not follow redirects, does not need credentials, and does not create an invitation. A successful readiness check is a prerequisite—not a substitute—for configuring the identity provider and invitation delivery bridge.

If it fails, open the Render service logs and use the failure message to determine whether startup, database connectivity, PostGIS, or schema migration needs attention. Do not run invitation acceptance until readiness passes and the separate identity/delivery integrations are configured.

## Deploy and configure the staging sign-in client

The Blueprint includes `ofi-staging-web`, a static, mobile-first sign-in page
under `web/`. After this PR is merged, open the existing Blueprint in Render,
tap **Manual sync**, and review the resource changes. It should add the static
site without deleting/recreating the existing API or database. Open the new
static site and copy the exact HTTPS hostname Render assigns it.

Then follow the [Google + SMS OTP Auth0 staging runbook](auth0-google-sms-staging.md):
create a dedicated Auth0 Single Page Application, register the static site's
exact origin in Allowed Callback URLs, Allowed Logout URLs, and Allowed Web
Origins, and enable Google + SMS connections for that application. Update
`web/auth-config.js` with only the public Auth0 tenant domain and SPA client ID,
then merge that configuration change and manually redeploy `ofi-staging-web`.

The client uses Auth0 Universal Login for both Google and SMS OTP; OFI never
collects SMS codes on its own. The Auth0 SDK keeps its token cache in memory,
and the page never prints access tokens. It intentionally does not call
protected OFI API routes in this milestone. The UI tells the user that
successful sign-in does not prove an OFI role or tenant membership.

The first sync can create the static website while sign-in remains disabled
because `web/auth-config.js` starts with empty tenant/client values. This is
intentional; do not put client secrets or SMS provider credentials in that file.

## Identity and invitations are still separate setup steps

The Blueprint keeps resource authentication enabled and does not publish an
identity issuer. `OFI_AUTH_ISSUER=https://ofi-staging.invalid` is a deliberately
non-routable placeholder. Before authenticating real test clients, configure
the real trusted identity provider's exact issuer, audience and JWKS URL, then
set `OFI_AUTH_MODE=oidc_jwks` and complete the
[identity-provider runbook](identity-provider.md). Do not disable resource
authentication on a reachable deployment.

Invitation delivery remains in `manual` mode because OFI does not include a
trusted mail/delivery bridge. Therefore the GitHub
`Staging invitation acceptance` workflow is **not expected to pass yet**.
Before that test, deploy a trusted bridge and configure
`OFI_INVITATION_DELIVERY_MODE=webhook`,
`OFI_INVITATION_DELIVERY_WEBHOOK_URL`, and
`OFI_INVITATION_DELIVERY_WEBHOOK_SECRET` on the API. The secret must be at
least 32 UTF-8 bytes. Follow the
[invitation delivery runbook](tenant-invitation-delivery.md); do not use a
public webhook inspector because the signed event body contains a usable
one-time invitation credential.

## Current acceptance boundary

This deployment gets the API online with PostgreSQL-backed schema state. It
does not establish live user authentication, trusted invitation delivery,
successful invitation redemption, backups, disaster recovery or production
readiness. Record each of those as a separate acceptance item.
