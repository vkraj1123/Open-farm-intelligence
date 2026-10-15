from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "staging_invitation_smoke.py"
SPEC = importlib.util.spec_from_file_location("ofi_staging_invitation_smoke", MODULE_PATH)
smoke = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(smoke)


@pytest.mark.parametrize(
    "url",
    [
        "http://staging.example.test",
        "https://user:pass@staging.example.test",
        "https://staging.example.test/path",
        "https://staging.example.test/?token=leak",
        "https://staging.example.test/#fragment",
        "https://localhost",
        "https://api.localhost",
        "https://127.0.0.1",
        "https://10.0.0.2",
        "https://[::1]",
        " https://staging.example.test",
    ],
)
def test_base_url_rejects_unsafe_or_ambiguous_targets(url):
    with pytest.raises(ValueError):
        smoke.validate_base_url(url)


def test_base_url_accepts_https_origin_and_removes_trailing_slash():
    assert smoke.validate_base_url("https://ofi-staging.example.test/") == "https://ofi-staging.example.test"


@pytest.mark.parametrize(
    "payload",
    [
        {"invitation_token": "one-time-secret"},
        {"invitation": {"token_sha256": "digest"}},
        [{"nested": {"delivery_secret": "private"}}],
        {"access-token": "secret"},
    ],
)
def test_response_secret_detector_rejects_token_like_fields(payload):
    assert smoke.contains_sensitive_field(payload)


def test_response_secret_detector_allows_metadata_only_response():
    assert not smoke.contains_sensitive_field({
        "invitation": {
            "invitation_id": "id",
            "tenant_id": "tenant",
            "invited_actor_id": "subject",
            "status": "pending",
        },
        "delivery": "accepted",
        "delivery_event_id": "a" * 64,
    })



def test_redirect_handler_never_returns_a_redirect_request():
    handler = smoke._NoRedirect()
    assert handler.redirect_request(
        None, None, 302, "Found", {}, "https://another-host.example.test"
    ) is None
