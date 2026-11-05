// Public configuration. OAuth Client ID is safe to publish; client secrets are not.
window.SOMAIYA_CONFIG = Object.freeze({
  googleClientId: "999197974175-pha6jti05qfs6hfr1qird7pklqgqe05s.apps.googleusercontent.com",
  // Campus-only network collector API (Network Monitoring), e.g. "https://netmon.<campus-domain>:8443".
  // Reachable only on the campus network; access is checked by the API against the IT Network Team list.
  netmonApiBase: ""
});
