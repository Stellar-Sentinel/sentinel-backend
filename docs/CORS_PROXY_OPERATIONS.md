# CORS and trusted-proxy operations

`CORS_ORIGINS` is an exact, comma-separated origin allowlist. Include the
scheme, host, and port when needed, for example
`https://dashboard.example.test`; do not use a wildcard for authenticated
browser traffic. An origin that merely shares a suffix with an allowed origin
must not be added implicitly.

`TRUSTED_PROXY_CIDRS` controls whether the rate limiter may use
`X-Forwarded-For`. With an empty setting, the direct TCP peer is the client
identity and forwarded headers are ignored. When proxies are configured, list
only the proxy IPs or CIDRs that the deployment controls and ensure the proxy
overwrites the forwarded chain rather than allowing a client to append an
untrusted hop.

The limiter walks the forwarded chain from the trusted edge toward the client
and stops at the first untrusted hop. This keeps client-controlled values out
of the key used for rate accounting. Invalid CIDR configuration fails startup
validation instead of silently widening trust.

## Deployment checklist

- Keep `CORS_ORIGINS` and `TRUSTED_PROXY_CIDRS` in deployment configuration,
  never in source control or request data.
- Test preflight from every approved frontend origin and from a lookalike origin
  before release.
- Confirm the gateway strips or overwrites `X-Forwarded-For` and that direct
  access to the application is blocked when proxy trust is enabled.
- Alert on rejected or exhausted screening requests using bounded status and
  route-template labels; do not log raw forwarded headers or account data.

These settings affect browser access and abuse-control identity only. They do
not change the Stellar contract ABI, and they do not establish that a screen
result is proof of fraud or compliance status.
