# Request security operations

Keep request correlation and browser-boundary middleware enabled on every
deployment. Verify a known request ID on health, business, error, and
unmatched routes after release. Invalid IDs must be replaced, never echoed.

For private deployments, configure a random API key of at least 32 characters
and send it only as `Authorization: Bearer <key>`. Health, documentation, and
CORS preflight remain public by design; business routes must return a generic
401 with `WWW-Authenticate: Bearer` for missing or invalid credentials.

Logs must retain only route templates, status, latency, and request IDs. Audit
collector filters so query strings, headers, bodies, account addresses, and
secrets cannot appear in the log stream.
