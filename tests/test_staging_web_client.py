"""Regression checks for the phone-first Auth0 browser client."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read_repo_file(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_browser_client_has_valid_public_auth0_spa_configuration():
    config = read_repo_file("web/auth-config.js")
    index = read_repo_file("web/index.html")
    domain = re.search(r'^\s*domain: "([^"]*)"', config, re.MULTILINE)
    client_id = re.search(r'^\s*clientId: "([^"]*)"', config, re.MULTILINE)
    assert domain is not None and re.fullmatch(r"[A-Za-z0-9.-]+", domain.group(1))
    assert "." in domain.group(1) and ".." not in domain.group(1)
    assert client_id is not None and client_id.group(1).strip()
    assert "clientSecret" not in config and "client_secret" not in config
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


def test_client_uses_official_auth0_cdn_global_namespace():
    index = read_repo_file("web/index.html")
    app = read_repo_file("web/app.js")
    assert "https://cdn.auth0.com/js/auth0-spa-js/2.24/auth0-spa-js.production.js" in index
    assert 'window.auth0.createAuth0Client' in app
    assert 'typeof window.auth0.createAuth0Client !== "function"' in app
    assert 'window.createAuth0Client' not in app


def test_auth0_initialization_does_not_request_ofi_api_audience():
    app = read_repo_file("web/app.js")
    init_block = app.split("async function init()", 1)[1].split("googleButton.addEventListener", 1)[0]
    client_setup = init_block.split("window.auth0.createAuth0Client({", 1)[1].split("});", 1)[0]
    assert "audience: config.audience" not in client_setup
    assert "The audience is requested only when starting login" in init_block
    assert "Allowed Callback URLs" in init_block
    assert "Allowed Logout URLs" in init_block
    assert "Allowed Web Origins" in init_block


def test_auth0_initialization_errors_identify_the_stage_without_leaking_tokens():
    app = read_repo_file("web/app.js")
    assert 'failedStage = "processing the Auth0 redirect callback"' in app
    assert 'failedStage = "checking the existing Auth0 session"' in app
    assert "authClient.getTokenSilently" in app
    assert "console.log" not in app


def test_identity_login_does_not_depend_on_ofi_api_audience():
    app = read_repo_file("web/app.js")
    login_block = app.split("async function startLogin(connection)", 1)[1].split("async function refreshSession()", 1)[0]
    assert "loginWithRedirect" in login_block
    assert "connection," in login_block
    assert "audience: config.audience" not in login_block
    assert "authClient.getTokenSilently" in app
