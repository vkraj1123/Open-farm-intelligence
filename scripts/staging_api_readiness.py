"""Phone-first, read-only smoke check for a deployed OFI Render API."""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 12
_RENDER_HOST_SUFFIX = ".onrender.com"
_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


class CheckFailure(RuntimeError):
    """Safe, operator-facing smoke-check failure without response bodies."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Do not follow redirects to a different host or an unexpected route."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_base_url(raw: str) -> str:
    """Accept only a root HTTPS origin assigned by Render.

    Limiting the check to a Render-hosted service avoids turning a manually
    dispatched GitHub workflow into a generic URL-fetching/SSRF primitive.
    A single trailing slash is harmless and is normalized away.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise CheckFailure("Provide the real HTTPS origin shown by Render.")

    value = raw.strip()
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise CheckFailure("The staging URL is malformed.") from None

    if parsed.scheme.lower() != "https" or not parsed.netloc or not hostname:
        raise CheckFailure("Use an HTTPS origin such as https://ofi-staging-api.onrender.com.")
    if parsed.username is not None or parsed.password is not None:
        raise CheckFailure("Credentials must not be embedded in the staging URL.")
    if port not in (None, 443):
        raise CheckFailure("The staging URL must use the default HTTPS port.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise CheckFailure("Enter only the service origin, without a path, query, or fragment.")

    host = hostname.lower()
    if host.endswith(".") or not host.endswith(_RENDER_HOST_SUFFIX):
        raise CheckFailure("Use the actual Render hostname ending in .onrender.com.")
    if host == "onrender.com" or host == _RENDER_HOST_SUFFIX.lstrip("."):
        raise CheckFailure("Use a specific Render service hostname, not the domain root.")
    if not _DOMAIN_RE.fullmatch(host):
        raise CheckFailure("The Render hostname contains invalid characters.")
    if any(part in {"example", "invalid", "test"} for part in host.split(".")):
        raise CheckFailure("A placeholder hostname is not a deployed service.")

    return f"https://{host}"


def fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS, opener=None) -> dict:
    """GET a small JSON response; reject redirects and never print response data."""
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "OFI-Staging-Readiness-Check/1.0",
        },
        method="GET",
    )
    client = opener or build_opener(_NoRedirectHandler)
    path = "/" + url.split("/", 3)[-1].split("?", 1)[0] if "://" in url else url

    try:
        response = client.open(request, timeout=timeout)
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            raise CheckFailure(f"{path} returned HTTP {exc.code}; redirects are not followed.") from None
        raise CheckFailure(f"{path} returned HTTP {exc.code}; expected HTTP 200.") from None
    except (URLError, TimeoutError, OSError, ssl.SSLError):
        raise CheckFailure(
            f"{path} could not be reached over verified HTTPS; check the hostname and deployment logs."
        ) from None

    try:
        with response:
            status = response.getcode()
            if status != 200:
                raise CheckFailure(f"{path} returned HTTP {status}; expected HTTP 200.")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except CheckFailure:
        raise
    except (OSError, TimeoutError, ssl.SSLError):
        raise CheckFailure(f"{path} failed while reading the response.") from None

    if len(body) > MAX_RESPONSE_BYTES:
        raise CheckFailure(f"{path} returned an unexpectedly large response.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CheckFailure(f"{path} did not return valid UTF-8 JSON.") from None
    if not isinstance(payload, dict):
        raise CheckFailure(f"{path} returned an unexpected JSON shape.")
    return payload


def verify_readiness_payload(payload: dict) -> tuple[int | None, int | None]:
    """Require durable PostgreSQL, PostGIS, and the current migration schema."""
    checks = payload.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    schema = checks.get("schema_version")
    schema = schema if isinstance(schema, dict) else {}
    missing = checks.get("missing_relations")
    missing_count = len(missing) if isinstance(missing, list) else None
    current = schema.get("current")
    expected = schema.get("expected")

    ready = (
        payload.get("status") == "ready"
        and payload.get("persistence") == "postgresql"
        and payload.get("durable") is True
        and checks.get("database_connection") is True
        and checks.get("postgis") is True
        and schema.get("ready") is True
        and missing == []
    )
    if not ready:
        raise CheckFailure(
            "/ready did not confirm durable PostgreSQL + PostGIS + current schema "
            f"(database_connection={checks.get('database_connection') is True}, "
            f"postgis={checks.get('postgis') is True}, "
            f"schema_version_ready={schema.get('ready') is True}, "
            f"missing_relations={missing_count if missing_count is not None else 'unknown'}). "
            "Do not run invitation acceptance yet."
        )
    return (current if isinstance(current, int) else None,
            expected if isinstance(expected, int) else None)


def run_check(base_url: str, fetcher=fetch_json) -> tuple[int | None, int | None]:
    """Verify both public endpoints; safe to run without credentials or test data."""
    base = validate_base_url(base_url)
    health = fetcher(base + "/health")
    if health.get("status") != "ok":
        raise CheckFailure("/health did not report status=ok.")
    readiness = fetcher(base + "/ready")
    return verify_readiness_payload(readiness)


def main() -> int:
    raw = os.getenv("OFI_STAGING_BASE_URL", "")
    try:
        current, expected = run_check(raw)
    except CheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS: /health returned status=ok (HTTP 200).")
    print("PASS: /ready confirmed PostgreSQL, PostGIS, durable storage, and all current schema relations.")
    if current is not None and expected is not None:
        print(f"PASS: migration schema version {current}/{expected}.")
    print("Staging API readiness is verified. Identity-provider and invitation delivery are separate steps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
