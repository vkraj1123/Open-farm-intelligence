(() => {
  "use strict";

  const config = window.OFI_AUTH_CONFIG || {};
  const googleButton = document.getElementById("google-login");
  const phoneButton = document.getElementById("phone-login");
  const logoutButton = document.getElementById("logout");
  const configNotice = document.getElementById("config-notice");
  const runtimeStatus = document.getElementById("runtime-status");
  const signedInPanel = document.getElementById("signed-in-panel");
  const userLabel = document.getElementById("user-label");
  const apiHealthLink = document.getElementById("api-health-link");
  let authClient = null;

  function setStatus(message, isError = false) {
    runtimeStatus.textContent = message;
    runtimeStatus.classList.toggle("notice", isError);
    runtimeStatus.classList.toggle("notice-error", isError);
  }

  function isConfigured() {
    return (
      typeof config.domain === "string" &&
      /^[A-Za-z0-9.-]+$/.test(config.domain) &&
      config.domain.includes(".") &&
      !config.domain.includes("..") &&
      typeof config.clientId === "string" &&
      config.clientId.trim().length > 0 &&
      typeof config.audience === "string" &&
      config.audience.trim().length > 0
    );
  }

  function updateHealthLink() {
    try {
      const url = new URL(config.apiBaseUrl);
      if (url.protocol === "https:" && url.pathname === "/" && !url.search && !url.hash) {
        apiHealthLink.href = url.origin + "/health";
      }
    } catch (_) {
      // Keep the known staging health link if optional config is malformed.
    }
  }

  async function startLogin(connection) {
    if (!authClient) {
      setStatus("Sign-in is not configured yet. Ask the OFI administrator to finish the staging Auth0 setup.", true);
      return;
    }

    googleButton.disabled = true;
    phoneButton.disabled = true;
    setStatus(connection === config.smsConnection
      ? "Opening the secure phone verification flow…"
      : "Opening secure Google sign-in…");

    try {
      await authClient.loginWithRedirect({
        // Establish the user's identity first. Request the OFI API audience
        // separately after login so an API configuration issue does not block
        // basic Google/SMS identity sign-in.
        authorizationParams: {
          connection,
          scope: "openid profile email"
        }
      });
    } catch (_) {
      googleButton.disabled = false;
      phoneButton.disabled = false;
      setStatus("Auth0 could not start sign-in. Check the configured tenant, connection, and allowed callback URL.", true);
    }
  }

  async function refreshSession() {
    const signedIn = await authClient.isAuthenticated();
    if (!signedIn) {
      signedInPanel.hidden = true;
      googleButton.disabled = false;
      phoneButton.disabled = false;
      setStatus("Ready when the Auth0 staging application is configured.");
      return;
    }

    const user = await authClient.getUser();
    signedInPanel.hidden = false;
    userLabel.textContent = user && (user.name || user.nickname)
      ? user.name || user.nickname
      : "Verified identity";
    googleButton.disabled = true;
    phoneButton.disabled = true;

    try {
      // Obtain the configured OFI API audience token, but never display, log,
      // persist manually, or send it to any endpoint in this client scaffold.
      await authClient.getTokenSilently({
        authorizationParams: { audience: config.audience }
      });
      setStatus("Identity-provider sign-in succeeded and Auth0 issued an access token for the configured API audience. OFI authorization and tenant membership remain unverified.");
    } catch (_) {
      setStatus("Identity-provider sign-in succeeded, but an access token for the configured OFI API audience was not issued. Check the Auth0 API Identifier and consent/configuration.", true);
    }
  }

  async function init() {
    updateHealthLink();

    if (!isConfigured()) {
      googleButton.disabled = true;
      phoneButton.disabled = true;
      configNotice.hidden = false;
      setStatus("Setup required: create an Auth0 Single Page Application, then add its public tenant domain and client ID to web/auth-config.js.", true);
      return;
    }

    configNotice.hidden = true;
    if (!window.auth0 || typeof window.auth0.createAuth0Client !== "function") {
      setStatus("The Auth0 browser SDK did not load. Check network access and reload this page.", true);
      return;
    }

    let failedStage = "creating the Auth0 browser client";
    try {
      authClient = await window.auth0.createAuth0Client({
        domain: config.domain,
        clientId: config.clientId,
        cacheLocation: "memory",
        useRefreshTokens: false,
        // Do not request an OFI API token during client initialization.
        // The audience is requested only during the post-login API-token check,
        // so an API setup issue cannot block identity sign-in.
        authorizationParams: {
          redirect_uri: window.location.origin,
          scope: "openid profile email"
        }
      });

      const params = new URLSearchParams(window.location.search);
      if (params.has("state") && (params.has("code") || params.has("error"))) {
        failedStage = "processing the Auth0 redirect callback";
        try {
          await authClient.handleRedirectCallback();
        } finally {
          // Remove OAuth response parameters; never leave them in the address bar.
          window.history.replaceState({}, document.title, window.location.pathname);
        }
      }

      googleButton.disabled = false;
      phoneButton.disabled = false;
      failedStage = "checking the existing Auth0 session";
      await refreshSession();
    } catch (_) {
      googleButton.disabled = true;
      phoneButton.disabled = true;
      if (failedStage === "creating the Auth0 browser client") {
        setStatus("Auth0 could not initialize. In Auth0 Application Settings, check the tenant domain and Client ID, then add https://ofi-staging-web.onrender.com to Allowed Callback URLs, Allowed Logout URLs, and Allowed Web Origins. Check Auth0 Dashboard → Monitoring → Logs for the exact cause.", true);
      } else if (failedStage === "processing the Auth0 redirect callback") {
        setStatus("Auth0 returned an error while completing sign-in. Check the Auth0 application callback URL and the tenant logs; no token was displayed or stored by OFI.", true);
      } else {
        setStatus("Auth0 initialized but could not read the current session. Check the Auth0 application’s Allowed Web Origins and recent tenant logs.", true);
      }
    }
  }

  googleButton.addEventListener("click", () => startLogin(config.googleConnection || "google-oauth2"));
  phoneButton.addEventListener("click", () => startLogin(config.smsConnection || "sms"));
  logoutButton.addEventListener("click", () => {
    if (!authClient) return;
    authClient.logout({
      logoutParams: { returnTo: window.location.origin }
    });
  });

  init();
})();
