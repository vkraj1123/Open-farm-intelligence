"""Regression checks for the unconfigured, phone-first Auth0 browser client."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_repo_file(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_browser_client_starts_disabled_without_auth0_tenant_values():
    config = read_repo_file("web/auth-config.js")
    index = read_repo_file("web/index.html")
    assert 'domain: ""' in config
    assert 'clientId: ""' in config
    assert 'audience: "ofi-api"' in config
    assert 'id="google-login" type="button" disabled' in index
    assert 'id="phone-login" type="button" disabled' in index


def test_login_client_supports_google_and_sms_connections():
    config = read_repo_file("web/auth-config.js")
    app = read_repo_file("web/app.js")
    assert 'googleConnection: "google-oauth2"' in config
    assert 'smsConnection: "sms"' in config
    assert 'startLogin(config.googleConnection || "google-oauth2")' in app
    assert 'startLogin(config.smsConnection || "sms")' in app
    assert "loginWithRedirect" in app
    assert "handleRedirectCallback" in app


def test_browser_token_handling_avoids_manual_persistence_or_logging():
    app = read_repo_file("web/app.js")
    assert 'cacheLocation: "memory"' in app
    assert "useRefreshTokens: false" in app
    assert "getTokenSilently" in app
    assert "console.log" not in app
    assert "localStorage" not in app
    assert "sessionStorage" not in app
    assert "token.textContent" not in app


def test_auth_ui_discloses_that_login_is_not_ofi_authorization():
    index = read_repo_file("web/index.html")
    app = read_repo_file("web/app.js")
    assert "OFI API permissions and tenant membership have not yet been tested" in index
    assert "OFI authorization and tenant membership remain unverified" in app
    assert 'rel="noopener noreferrer"' in index


def test_render_blueprint_declares_static_client_and_security_headers():
    render = read_repo_file("render.yaml")
    assert "name: ofi-staging-web" in render
    assert "runtime: static" in render
    assert "staticPublishPath: ./web" in render
    assert "Content-Security-Policy" in render
    assert "X-Content-Type-Options" in render
    assert "value: hs256" in render
