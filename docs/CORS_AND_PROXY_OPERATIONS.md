# CORS and trusted proxy operations

This service has two independent boundaries:

- CORS controls which browser origins may read API responses.
- Trusted proxy configuration controls when the screening rate limiter may use
  `X-Forwarded-For` to identify the client.

Neither setting authenticates a caller. Use `API_KEY` for private deployments,
and keep it out of browser code.

## Configuration contract

`CORS_ORIGINS` is a comma-separated allow-list of exact origins. An origin is
the scheme, host, and optional port; paths and trailing slashes are not part of
an origin. For example:

```dotenv
CORS_ORIGINS=https://dashboard.example.com,http://localhost:3000
```

The default is `http://localhost:3000`. Wildcards are not used, credentials are
disabled, and the allowed methods are `GET`, `POST`, and `OPTIONS`. The API
allows `Content-Type` and `Authorization` request headers and exposes the rate
limit headers listed in the main README. A browser origin omitted from the list
receives no `Access-Control-Allow-Origin` response header; same-origin and
non-browser clients are not changed by CORS.

`TRUSTED_PROXY_CIDRS` is a comma-separated list of IPs or CIDRs for the
reverse proxies that connect directly to this application:

```dotenv
TRUSTED_PROXY_CIDRS=10.20.0.0/24,2001:db8:1234::/48
```

The default is empty, so the rate limiter uses the ASGI direct peer and ignores
`X-Forwarded-For`. When the direct peer is in a configured range, the middleware
walks the forwarded chain from the proxy inward and selects the first untrusted
address. A malformed chain falls back to the direct peer. This behavior assumes
the proxy overwrites or appends a single authoritative `X-Forwarded-For` chain;
do not add a broad cloud/VPC range unless every host in that range is a trusted
proxy.

The setting currently affects only the in-process `POST /risk/score` limiter.
It does not rewrite application request scope, change access logs, or make
forwarded headers trustworthy for other features. The limiter is process-local,
so multi-worker or multi-replica deployments should enforce a shared limit at
the gateway as well.

## Concrete checks

After deploying, replace the API and dashboard origins with the actual values:

```bash
API=https://api.example.com
ORIGIN=https://dashboard.example.com

curl -i -X OPTIONS "$API/risk/score" \
  -H "Origin: $ORIGIN" \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type,authorization'
```

The response should be successful and include
`Access-Control-Allow-Origin: https://dashboard.example.com`. An unlisted
origin must not receive that header. A real screening request uses the same
origin and remains subject to the configured rate limit:

```bash
curl -i "$API/health" -H "Origin: $ORIGIN"
curl -i -X POST "$API/risk/score" \
  -H "Origin: $ORIGIN" -H 'Content-Type: application/json' \
  --data '{"address":"GAAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQDZ7H"}'
```

Do not use a browser or a client-supplied `X-Forwarded-For` value as proof that
the proxy configuration is correct. Validate the direct peer and proxy header
behavior in the deployment platform's request logs, and keep proxy ranges in
infrastructure configuration under review.

## Deploy and rollback

1. Record the current `CORS_ORIGINS`, `TRUSTED_PROXY_CIDRS`, API image/commit,
   and health-check result before changing configuration.
2. Deploy the reviewed commit with the environment values set in the platform
   secret/configuration store. Never put API keys or signing seeds in this
   document or in a repository file.
3. Run the preflight and `/health` checks above, then verify an unlisted origin
   is denied. If proxy behavior changed, also verify a request through the
   intended proxy and confirm the rate-limit client identity.
4. Roll back by redeploying the previous known-good commit and restoring the
   recorded configuration values. Re-run the same checks; configuration rollback
   is required if the change was an environment-only change.

If the dashboard stops making requests, first check an exact origin mismatch
(scheme, host, or port), then inspect the proxy's forwarded-header policy. If
rate limits group all users together, leave `TRUSTED_PROXY_CIDRS` empty until the
direct peer and overwrite behavior are confirmed.

## Coordination and limitations

The frontend contract is the API origin in
[`stellar-frontend`](https://github.com/Stellar-Sentinel/sentinel-frontend):
its `NEXT_PUBLIC_API_BASE_URL` must point to the deployed API, and that exact
origin must be in `CORS_ORIGINS`. No frontend code change is required for this
documentation milestone.

The backend reads contract events but this change does not alter the Stellar
contract ABI or event schema. Contract deployment and rollback remain covered
by the [`sentinel-contracts` deployment runbook](https://github.com/Stellar-Sentinel/sentinel-contracts#testnet-deployment).
