# Readiness operations

Use `/live` to determine whether the process can serve requests and `/ready` to
determine whether both configured upstream dependencies are available. A
healthy process may therefore be live while not ready during Horizon or
Soroban RPC degradation.

## Deployment behavior

- HTTP `200` from `/ready` means both dependency labels are `ok`.
- HTTP `503` means at least one dependency is `unavailable`.
- The response contains stable dependency labels only; upstream exception
  details are intentionally omitted.
- Probes are uncached and bounded by `REQUEST_TIMEOUT_SECONDS` plus the shared
  read-only retry limits.
- A readiness failure should remove the instance from traffic, not imply the
  process needs to be restarted.

Health checks should use the exact configured route and should not treat a
successful `/health` or `/live` response as evidence that Horizon or Soroban
RPC is reachable. Synthetic fake dependencies are sufficient for tests; probes
must never use signing keys or live-chain calls in CI.
