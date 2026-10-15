"""Offline regression tests for the phone-launchable staging API check."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

# Load the phone-invoked script directly; pytest's configured pythonpath is src/.
_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "staging_api_readiness.py"
_SPEC = spec_from_file_location("staging_api_readiness", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_readiness = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_readiness)

CheckFailure = _readiness.CheckFailure
MAX_RESPONSE_BYTES = _readiness.MAX_RESPONSE_BYTES
_NoRedirectHandler = _readiness._NoRedirectHandler
fetch_json = _readiness.fetch_json
run_check = _readiness.run_check
validate_base_url = _readiness.validate_base_url
verify_readiness_payload = _readiness.verify_readiness_payload


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://ofi-staging-api.onrender.com", "https://ofi-staging-api.onrender.com"),
        (" https://ofi-staging-api.onrender.com/ ", "https://ofi-staging-api.onrender.com"),
        ("https://service-123.onrender.com:443", "https://service-123.onrender.com"),
    ],
)
def test_validate_base_url_accepts_a_render_origin(raw, expected):
    assert validate_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ofi-staging-api.onrender.com",
        "http://ofi-staging-api.onrender.com",
        "https://onrender.com",
        "https://example.net",
        "https://ofi-staging.example.net",
        "https://localhost",
        "https://127.0.0.1",
        "https://user:secret@ofi-staging-api.onrender.com",
        "https://ofi-staging-api.onrender.com/api",
        "https://ofi-staging-api.onrender.com?token=secret",
        "https://ofi-staging-api.onrender.com/#fragment",
        "https://ofi-staging-api.onrender.com:8443",
        "https://ofi-staging-api.onrender.com.",
        "https://bad host.onrender.com",
    ],
)
def test_validate_base_url_rejects_unsafe_or_ambiguous_targets(raw):
    with pytest.raises(CheckFailure):
        validate_base_url(raw)


def ready_payload(**overrides):
    payload = {
        "status": "ready",
        "persistence": "postgresql",
        "durable": True,
        "checks": {
            "database_connection": True,
            "postgis": True,
            "schema_version": {"current": 12, "expected": 12, "ready": True},
            "missing_relations": [],
        },
    }
    payload.update(overrides)
    return payload


def test_readiness_requires_postgresql_postgis_and_current_schema():
    assert verify_readiness_payload(ready_payload()) == (12, 12)


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "not_ready", "persistence": "postgresql", "durable": True},
        {"status": "ready", "persistence": "memory", "durable": False},
        ready_payload(checks={
            "database_connection": True,
            "postgis": False,
            "schema_version": {"current": 12, "expected": 12, "ready": True},
            "missing_relations": [],
        }),
        ready_payload(checks={
            "database_connection": True,
            "postgis": True,
            "schema_version": {"current": 11, "expected": 12, "ready": False},
            "missing_relations": [],
        }),
        ready_payload(checks={
            "database_connection": True,
            "postgis": True,
            "schema_version": {"current": 12, "expected": 12, "ready": True},
            "missing_relations": ["farms"],
        }),
    ],
)
def test_readiness_rejects_non_durable_or_incomplete_schema(payload):
    with pytest.raises(CheckFailure, match="/ready"):
        verify_readiness_payload(payload)


def test_run_check_calls_health_then_ready_without_credentials():
    calls = []

    def fetcher(url):
        calls.append(url)
        if url.endswith("/health"):
            return {"status": "ok"}
        return ready_payload()

    assert run_check("https://ofi-staging-api.onrender.com", fetcher) == (12, 12)
    assert calls == [
        "https://ofi-staging-api.onrender.com/health",
        "https://ofi-staging-api.onrender.com/ready",
    ]


def test_run_check_stops_when_health_fails():
    calls = []

    def fetcher(url):
        calls.append(url)
        return {"status": "degraded"}

    with pytest.raises(CheckFailure, match="/health"):
        run_check("https://ofi-staging-api.onrender.com", fetcher)
    assert len(calls) == 1


def test_redirect_handler_never_creates_a_followup_request():
    handler = _NoRedirectHandler()
    assert handler.redirect_request(
        Request("https://ofi-staging-api.onrender.com/health"),
        None,
        302,
        "Found",
        {},
        "https://unexpected.example/health",
    ) is None


def test_fetch_json_rejects_redirects_without_following_them():
    class RedirectingOpener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 302, "Found", {}, None)

    with pytest.raises(CheckFailure, match="redirects are not followed"):
        fetch_json("https://ofi-staging-api.onrender.com/health", opener=RedirectingOpener())


def test_fetch_json_rejects_oversized_responses(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 200

        def read(self, limit):
            assert limit == MAX_RESPONSE_BYTES + 1
            return b"x" * limit

    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse()

    with pytest.raises(CheckFailure, match="unexpectedly large"):
        fetch_json("https://ofi-staging-api.onrender.com/health", opener=FakeOpener())
