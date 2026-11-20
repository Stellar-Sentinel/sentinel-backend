# API-key authentication contract

`API_KEY` is optional for local development and required for private
deployments. When configured, business routes require an
`Authorization: Bearer <API_KEY>` header. Comparison is constant-time, and
missing, malformed, or incorrect credentials receive the same generic 401
response with `WWW-Authenticate: Bearer`.

Health, API documentation, and CORS preflight are intentionally public so
probes and browser negotiation continue to work. This exception does not
apply to screening, account, event, or network business routes.

Keys must be at least 32 characters, stored outside source control, and never
written to logs or query parameters. Rotate a key through the deployment
secret manager and verify both an authorized and unauthorized request after
rotation.
