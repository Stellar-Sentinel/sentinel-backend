# API-key operations

For a private deployment, generate a random API key of at least 32 characters
in the secret manager. Set `API_KEY` at deploy time and send it as a bearer
header from trusted clients; do not put it in URLs, browser storage, source
control, or logs.

After deployment, verify that `/health`, `/docs`, and CORS `OPTIONS` remain
available, while `/risk/score`, `/accounts`, `/events`, and `/network/status`
reject missing and wrong credentials with 401. Verify a correct bearer key
works, then remove old credentials after rotation.

If a key may have leaked, revoke it in the secret manager, redeploy, and audit
access logs for the affected window. The middleware must not reveal whether a
credential was missing or merely incorrect.
