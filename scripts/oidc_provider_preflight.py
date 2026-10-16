"""Phone-first, read-only OIDC issuer/JWKS metadata preflight.

This checks public provider metadata only. It does not mint or validate a
bearer token, configure the OFI deployment, or prove tenant authorization.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 8
MAX_JWKS_KEYS = 1000
_PRIVATE_JWK_FIELDS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})
_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PreflightFailure(RuntimeError):
    """Sanitized operator-facing error; never includes response body or key data."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_https_url(raw: str, label: str) -> str:
    """Validate a public-looking HTTPS provider URL without credentials or query."""
    if not isinstance(raw, str) or not raw.strip():
        raise PreflightFailure(f"Provide the provider's {label} HTTPS URL.")
    value = raw.strip()
    if len(value) > 2048:
        raise PreflightFailure(f"The provider's {label} URL is too long.")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise PreflightFailure(f"The provider's {label} URL is malformed.") from None

    if parsed.scheme.lower() != "https" or not parsed.netloc or not hostname:
        raise PreflightFailure(f"The provider's {label} URL must use HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise PreflightFailure(f"The provider's {label} URL must not contain credentials.")
    if port not in (None, 443):
        raise PreflightFailure(f"The provider's {label} URL must use the default HTTPS port.")
    if parsed.query or parsed.fragment:
        raise PreflightFailure(f"The provider's {label} URL must not contain a query or fragment.")

    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise PreflightFailure(f"The provider's {label} hostname is malformed.") from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise PreflightFailure(f"The provider's {label} URL must use a DNS hostname, not an IP address.")

    if host.endswith(".") or not _DOMAIN_RE.fullmatch(host) or "." not in host:
        raise PreflightFailure(f"The provider's {label} URL must use a fully qualified DNS hostname.")
    if host in {"example.com", "example.net", "example.org"} or any(
        host == suffix or host.endswith("." + suffix)
        for suffix in ("example", "invalid", "test", "localhost", "local", "internal", "onion")
    ):
        raise PreflightFailure(f"The provider's {label} URL contains a placeholder or non-public hostname.")

    return value


def _discovery_url(issuer: str) -> str:
    """Build the OIDC discovery URL while preserving the issuer path."""
    suffix = ".well-known/openid-configuration"
    return issuer + suffix if issuer.endswith("/") else issuer + "/" + suffix


def _assert_public_dns(hostname: str) -> None:
    """Refuse provider names that resolve to private, loopback, or special-use IPs."""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise PreflightFailure("Provider metadata must use a public DNS hostname, not an IP address.")

    try:
        records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise PreflightFailure("Provider hostname did not resolve; check the issuer and JWKS URLs.") from None
    addresses = {record[4][0].split("%", 1)[0] for record in records}
    if not addresses:
        raise PreflightFailure("Provider hostname returned no DNS addresses.")
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError:
            raise PreflightFailure("Provider hostname returned an invalid DNS address.") from None
        if not parsed_address.is_global:
            raise PreflightFailure("Provider hostname resolves to a non-public IP address; refusing the request.")


def fetch_json(
    url: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener=None,
    host_validator=_assert_public_dns,
) -> dict:
    """Fetch a small JSON object using verified TLS; reject redirects and large bodies."""
    try:
        hostname = urlsplit(url).hostname
    except ValueError:
        hostname = None
    if not hostname:
        raise PreflightFailure("Provider metadata URL has no valid hostname.")
    host_validator(hostname)

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "OFI-OIDC-Provider-Preflight/1.0",
        },
        method="GET",
    )
    client = opener or build_opener(_NoRedirectHandler)
    try:
        response = client.open(request, timeout=timeout)
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            raise PreflightFailure("Provider metadata returned a redirect; redirects are not followed.") from None
        raise PreflightFailure(f"Provider metadata returned HTTP {exc.code}; expected HTTP 200.") from None
    except ssl.SSLError:
        raise PreflightFailure("Provider metadata failed TLS certificate verification.") from None
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLError):
            raise PreflightFailure("Provider metadata failed TLS certificate verification.") from None
        raise PreflightFailure("Provider metadata could not be reached over verified HTTPS.") from None

    try:
        with response:
            if response.getcode() != 200:
                raise PreflightFailure("Provider metadata returned a non-200 HTTP response.")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except PreflightFailure:
        raise
    except (OSError, TimeoutError, ssl.SSLError):
        raise PreflightFailure("Provider metadata failed while reading the response.") from None

    if len(body) > MAX_RESPONSE_BYTES:
        raise PreflightFailure("Provider metadata exceeded the allowed response size.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PreflightFailure("Provider metadata did not contain valid UTF-8 JSON.") from None
    if not isinstance(payload, dict):
        raise PreflightFailure("Provider metadata has an unexpected JSON shape.")
    return payload


def _decode_base64url_integer(value: object, label: str) -> int:
    if not isinstance(value, str) or not _BASE64URL_RE.fullmatch(value):
        raise PreflightFailure(f"A candidate RS256 key has an invalid public {label} value.")
    if len(value) % 4 == 1:
        raise PreflightFailure(f"A candidate RS256 key has an invalid public {label} value.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, base64.binascii.Error):
        raise PreflightFailure(f"A candidate RS256 key has an invalid public {label} value.") from None
    if not decoded:
        raise PreflightFailure(f"A candidate RS256 key has an empty public {label} value.")
    return int.from_bytes(decoded, "big")


def verify_jwks(payload: dict) -> int:
    """Require at least one unique, public-only RSA key suitable for RS256."""
    keys = payload.get("keys")
    if not isinstance(keys, list) or not keys or len(keys) > MAX_JWKS_KEYS:
        raise PreflightFailure("The JWKS document has a missing, empty, or oversized keys list.")

    compatible = []
    for key in keys:
        if not isinstance(key, dict):
            raise PreflightFailure("The JWKS document contains a malformed key entry.")
        if _PRIVATE_JWK_FIELDS.intersection(key):
            raise PreflightFailure("The JWKS document exposes private or symmetric key material; stop and notify the provider owner.")
        if key.get("kty") != "RSA":
            continue
        if key.get("use") not in (None, "sig") or key.get("alg") not in (None, "RS256"):
            continue

        kid = key.get("kid")
        if not isinstance(kid, str) or not kid.strip():
            raise PreflightFailure("A candidate RS256 key is missing its key ID (kid).")
        modulus = _decode_base64url_integer(key.get("n"), "modulus")
        exponent = _decode_base64url_integer(key.get("e"), "exponent")
        if modulus.bit_length() < 2048:
            raise PreflightFailure("A candidate RS256 RSA key is smaller than the 2048-bit minimum.")
        if exponent < 3 or exponent % 2 == 0:
            raise PreflightFailure("A candidate RS256 RSA key has an invalid public exponent.")
        compatible.append(kid)

    if not compatible:
        raise PreflightFailure("The JWKS document has no usable public RSA signing key compatible with RS256.")
    if len(set(compatible)) != len(compatible):
        raise PreflightFailure("The JWKS document has duplicate key IDs among RS256-compatible RSA keys.")
    return len(compatible)


def run_preflight(issuer: str, jwks_url: str, fetcher=fetch_json) -> int:
    """Verify discovery issuer/JWKS consistency and an RS256-compatible public key set."""
    validated_issuer = validate_https_url(issuer, "issuer")
    validated_jwks = validate_https_url(jwks_url, "JWKS")
    discovery = fetcher(_discovery_url(validated_issuer))

    discovered_issuer = discovery.get("issuer")
    if not isinstance(discovered_issuer, str) or discovered_issuer != validated_issuer:
        raise PreflightFailure("OIDC discovery issuer does not exactly match the configured issuer.")
    discovered_jwks = discovery.get("jwks_uri")
    if not isinstance(discovered_jwks, str) or discovered_jwks != validated_jwks:
        raise PreflightFailure("OIDC discovery jwks_uri does not exactly match the configured JWKS URL.")

    return verify_jwks(fetcher(validated_jwks))


def main() -> int:
    import os
    import sys

    issuer = os.getenv("OFI_OIDC_ISSUER", "")
    jwks_url = os.getenv("OFI_OIDC_JWKS_URL", "")
    try:
        count = run_preflight(issuer, jwks_url)
    except PreflightFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS: OIDC discovery issuer exactly matches the configured issuer.")
    print("PASS: discovery JWKS endpoint exactly matches the configured HTTPS URL.")
    print(f"PASS: JWKS contains {count} unique public RSA signing key(s) suitable for RS256 (minimum 2048-bit modulus).")
    print("Provider metadata preflight passed. This does not test user login, bearer tokens, audience/claims, or OFI tenant membership.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
