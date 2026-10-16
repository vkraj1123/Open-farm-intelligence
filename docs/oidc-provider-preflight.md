# Phone-first OIDC provider metadata preflight

Use this read-only check before changing OFI's staging authentication settings.
It validates the public metadata that OFI's RS256/JWKS verifier depends on,
without issuing tokens, sending credentials, or changing Render.

## Before you run it

Choose the identity provider that will own staging user sign-in and token
issuance. OFI does not supply an identity provider. You will need the provider's
exact **issuer** URL and its **JWKS URI**.

1. Open the identity provider's OIDC discovery document in your phone browser.
   Its URL is the issuer URL followed by `/.well-known/openid-configuration`
   (when the issuer already ends in `/`, append `.well-known/openid-configuration`
   without adding a second slash).
2. In that JSON document, copy the exact `issuer` value and the exact
   `jwks_uri` value. Do not guess either URL; the preflight compares both
   values exactly.
3. Do not copy client secrets, access tokens, refresh tokens, private keys or
   credentials into workflow inputs, issues, source code, or chat.

## Run from GitHub on Android

1. Open the [OIDC provider metadata preflight workflow](https://github.com/vkraj1123/Open-farm-intelligence/actions/workflows/oidc-provider-preflight.yml).
2. Tap **Run workflow** and select branch `main`.
3. Paste the exact discovery-document `issuer` into the **issuer** input.
4. Paste the exact discovery-document `jwks_uri` into the **jwks_url** input.
5. Tap **Run workflow** and inspect the final step.

The workflow uses only a GitHub-hosted runner, Python's standard library and
read-only public HTTPS requests. It needs no GitHub variables/secrets, Render
secret, admin token, client secret, or provider account credential. TLS
certificate verification stays enabled and HTTP redirects are rejected.
It rejects credentials/query strings/fragments, non-HTTPS URLs, IP-literal and
placeholder/non-public hostname patterns. Network responses are bounded in
size, and response bodies/key material are never printed.

## What a pass establishes

- The discovery document's `issuer` exactly equals the supplied issuer.
- The discovery document's `jwks_uri` exactly equals the supplied JWKS URL.
- The JWKS document has at least one unique, public-only RSA signing key
  compatible with RS256 and a modulus of at least 2048 bits.
- The metadata endpoints were reachable with verified HTTPS at the time of the
  check.

## What a pass does not establish

This is provider-metadata preflight only. It does **not** validate a real
access token, configured API audience, role/tenant claims, MFA, account
verification, subject mapping, user membership, browser login, or invitation
delivery/redemption. It does not change Render's environment variables.

Do not change the running API from `OFI_AUTH_MODE=hs256` to
`OFI_AUTH_MODE=oidc_jwks` merely because this check passes. First review the
provider's token configuration, make sure it issues RS256 access tokens with
the exact issuer/audience expected by OFI and trusted role/tenant claims, then
configure `OFI_AUTH_ISSUER`, `OFI_AUTH_AUDIENCE`, and
`OFI_AUTH_JWKS_URL` using the Render dashboard's environment settings. Keep
tenant-membership enforcement enabled. Test using only disposable staging
identities; never place production credentials or real farmer records in the
temporary Free-tier staging service.

For the API's precise accepted token profile, see
[OIDC/JWKS bearer verification](identity-provider.md). The selected provider's
real end-to-end login/token test and the separate signed invitation delivery
bridge remain release gates.
