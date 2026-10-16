"""Offline tests for the read-only OIDC/JWKS metadata preflight."""

import base64
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "oidc_provider_preflight.py"
_SPEC = spec_from_file_location("oidc_provider_preflight", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_preflight = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_preflight)

PreflightFailure = _preflight.PreflightFailure
MAX_RESPONSE_BYTES = _preflight.MAX_RESPONSE_BYTES
_NoRedirectHandler = _preflight._NoRedirectHandler
fetch_json = _preflight.fetch_json
run_preflight = _preflight.run_preflight
validate_https_url = _preflight.validate_https_url
verify_jwks = _preflight.verify_jwks


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def public_rsa_key(kid="staging-key-1", bits=2048, **extra):
    byte_length = (bits + 7) // 8
    first = 1 << ((bits - 1) % 8)
    modulus_bytes = bytes([first]) + bytes([0x51]) * (byte_length - 1)
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": b64url(modulus_bytes),
        "e": b64url((65537).to_bytes(3, "big")),
        **extra,
    }


ISSUER = "https://login.example-idp.net/tenant"
JWKS_URL = "https://keys.example-idp.net/tenant/keys"


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        (ISSUER, "issuer"),
        (ISSUER + "/", "issuer"),
        (JWKS_URL, "JWKS"),
        (" https://login.example-idp.net/tenant ", "issuer"),
    ],
)
def test_validate_https_url_accepts_provider_urls(raw, label):
    assert validate_https_url(raw, label) == raw.strip()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "login.example-idp.net",
        "http://login.example-idp.net",
        "https://user:secret@login.example-idp.net/tenant",
        "https://login.example-idp.net/tenant?token=secret",
        "https://login.example-idp.net/tenant#fragment",
        "https://login.example-idp.net:8443/tenant",
        "https://localhost/tenant",
        "https://service.local/tenant",
        "https://idp.invalid/tenant",
        "https://example.com/tenant",
        "https://127.0.0.1/tenant",
        "https://[::1]/tenant",
        "https://singlelabel/tenant",
        "https://bad host.example-idp.net/tenant",
    ],
)
def test_validate_https_url_rejects_unsafe_or_ambiguous_urls(raw):
    with pytest.raises(PreflightFailure):
        validate_https_url(raw, "issuer")


def discovery_and_jwks(issuer=ISSUER, jwks_url=JWKS_URL, keys=None):
    def fetcher(url):
        if url == _preflight._discovery_url(ISSUER):
            return {"issuer": issuer, "jwks_uri": jwks_url}
        if url == JWKS_URL:
            return {"keys": keys if keys is not None else [public_rsa_key()]}
        raise AssertionError(f"Unexpected URL requested: {url}")
    return fetcher


def test_run_preflight_accepts_matching_discovery_and_public_rs256_key():
    assert run_preflight(ISSUER, JWKS_URL, discovery_and_jwks()) == 1


def test_discovery_endpoint_handles_issuer_with_trailing_slash():
    issuer = ISSUER + "/"
    requested = []

    def fetcher(url):
        requested.append(url)
        if url == _preflight._discovery_url(issuer):
            return {"issuer": issuer, "jwks_uri": JWKS_URL}
        if url == JWKS_URL:
            return {"keys": [public_rsa_key()]}
        raise AssertionError("Unexpected URL")

    assert run_preflight(issuer, JWKS_URL, fetcher) == 1
    assert requested[0] == ISSUER + "/.well-known/openid-configuration"


def test_discovery_issuer_must_match_exactly():
    with pytest.raises(PreflightFailure, match="issuer does not exactly match"):
        run_preflight(
            ISSUER,
            JWKS_URL,
            discovery_and_jwks(issuer=ISSUER + "/"),
        )


def test_discovery_jwks_uri_must_match_exactly():
    with pytest.raises(PreflightFailure, match="jwks_uri does not exactly match"):
        run_preflight(
            ISSUER,
            JWKS_URL,
            discovery_and_jwks(jwks_url="https://keys.example-idp.net/other"),
        )


def test_jwks_rejects_small_rsa_keys():
    with pytest.raises(PreflightFailure, match="smaller than the 2048-bit minimum"):
        verify_jwks({"keys": [public_rsa_key(bits=1024)]})


def test_jwks_rejects_private_key_material():
    with pytest.raises(PreflightFailure, match="private or symmetric key material"):
        verify_jwks({"keys": [public_rsa_key(d="private-value")] })


def test_jwks_rejects_duplicate_compatible_key_ids():
    with pytest.raises(PreflightFailure, match="duplicate key IDs"):
        verify_jwks({"keys": [public_rsa_key(), public_rsa_key()] })


def test_jwks_requires_a_compatible_rsa_signing_key():
    with pytest.raises(PreflightFailure, match="no usable public RSA signing key"):
        verify_jwks({"keys": [{"kty": "EC", "use": "sig", "kid": "ec-1"}]})


def test_redirect_handler_never_follows_redirect():
    handler = _NoRedirectHandler()
    assert handler.redirect_request(
        Request(ISSUER + "/.well-known/openid-configuration"),
        None,
        302,
        "Found",
        {},
        "https://redirect.example-idp.net/metadata",
    ) is None


def test_fetch_json_rejects_redirects():
    class RedirectingOpener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 302, "Found", {}, None)

    with pytest.raises(PreflightFailure, match="redirect; redirects are not followed"):
        fetch_json(ISSUER, opener=RedirectingOpener())


def test_fetch_json_rejects_oversized_response():
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

    with pytest.raises(PreflightFailure, match="exceeded the allowed response size"):
        fetch_json(ISSUER, opener=FakeOpener())
