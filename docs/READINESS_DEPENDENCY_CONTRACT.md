# Readiness dependency contract

`GET /ready` is a deployment readiness probe, not a liveness probe. It checks
the configured Horizon fee-stats endpoint and Soroban RPC health method on each
request using the configured outbound timeout and retry bounds.

## Response contract

When both dependencies respond successfully:

```json
{"status":"ready","dependencies":{"horizon":"ok","soroban_rpc":"ok"}}
```

When either dependency is unavailable, the endpoint returns HTTP `503`:

```json
{"status":"not_ready","dependencies":{"horizon":"ok","soroban_rpc":"unavailable"}}
```

Dependency values are fixed labels. Upstream exception messages, URLs, response
bodies, credentials, and signing material are never returned. The endpoint is
not cached so a deployment observes current dependency state; `/health` and
`/live` remain dependency-free process checks.

## Operational boundaries

Readiness probes are bounded by `REQUEST_TIMEOUT_SECONDS` and the shared
read-only retry policy. Tests must fake both dependency calls, cover healthy,
mixed, and fully degraded states, and avoid live-chain traffic. Monitoring and
deployment systems should treat `200` as ready and `503` as degraded or
unavailable, without interpreting the response as an application health score.
