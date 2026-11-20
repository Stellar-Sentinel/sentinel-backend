# Deployment configuration contract

The settings model is the boundary between deployment-provided environment
variables and application behavior. A deployment is valid only when all of the
following invariants hold.

## Validation invariants

- Every numeric setting is finite and within its declared Pydantic bounds.
- `REQUEST_TIMEOUT_SECONDS` is between 0.1 and 60 seconds.
- Retry counts and backoff/cap values stay within their declared limits; retry
  behavior cannot create an unbounded request budget.
- `RISK_HIGH_SCORE_THRESHOLD` is strictly greater than
  `RISK_ELEVATED_SCORE_THRESHOLD`.
- `RISK_POLICY_VERSION` is a three-part semantic version (`X.Y.Z`).
- Each value in `TRUSTED_PROXY_CIDRS` is a valid IP network. An empty value
  means forwarded client headers are not trusted.
- In production, `API_KEY` has at least 32 characters and no CORS origin is a
  local development host (`localhost` or `127.0.0.1`).

## Deployment invariants

- Horizon URL, Soroban RPC URL, and network passphrase identify one network.
- `CONTRACT_ID` is required for event-history behavior; without it, `/events`
  fails closed instead of inventing contract data.
- Secrets, signing keys, and tokens are supplied by the deployment secret store
  and are never committed or emitted in logs.
- `AGENT_ENABLED=true` is valid only with an authorized `AGENT_SECRET_KEY`.
  The agent's key must be dedicated to the configured contract and network.
- A readiness failure is a deployment failure: traffic must not be promoted
  while `/ready` reports an unavailable dependency.

## Change and compatibility rules

Adding a setting requires a safe default, a documented environment variable,
validation bounds, and focused test coverage. Tightening a bound is a
configuration compatibility change and must be called out in release notes.
Changing network, contract, authentication, or agent settings requires a
review of the deployment secret store and a readiness check after rollout.

The contract is intentionally fail-closed. Unknown environment variables are
ignored by the settings model, but known variables with invalid values must
prevent startup rather than being silently coerced.
