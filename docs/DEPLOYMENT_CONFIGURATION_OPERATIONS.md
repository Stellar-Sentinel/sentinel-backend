# Deployment configuration validation operations

This service validates environment-backed settings when the application starts.
Treat a failed startup as a configuration error, not as a reason to relax a
validator or substitute a default in the deployment platform.

## Pre-deployment checklist

1. Set `ENVIRONMENT=production` for a production deployment.
2. Provide an `API_KEY` with at least 32 characters. Keep it in the platform's
   secret store; do not put it in `.env.example`, logs, or a PR.
3. Set `CORS_ORIGINS` to the exact browser origins that are allowed. Production
   values must not contain `localhost` or `127.0.0.1`.
4. Set `HORIZON_URL`, `SOROBAN_RPC_URL`, and `NETWORK_PASSPHRASE` to the same
   Stellar network. Set `CONTRACT_ID` when `/events` is enabled.
5. Review numeric limits against the platform's request and memory budgets.
   The settings model rejects values outside the documented bounds.
6. If using the monitoring agent, set `AGENT_ENABLED=true` only after the
   configured `AGENT_SECRET_KEY` public key is authorized by the contract.

## Release procedure

Validate the exact environment before switching traffic to a new revision.
The application must be allowed to start normally so `Settings` can reject an
invalid value. A deployment that cannot construct settings should remain out
of service and should not receive production traffic.

After startup, check:

- `GET /health` for process liveness;
- `GET /ready` for Horizon and Soroban dependency readiness; and
- one authenticated business-route request when `API_KEY` is configured.

`/health` is not a substitute for `/ready`: liveness can succeed while an
upstream dependency is unavailable. Do not put secrets or full environment
values in health-check output or deployment logs.

## Safe rollback

Keep the previous revision and its known-good configuration available until the
new revision has passed readiness and smoke checks. Roll back the application
and configuration together when a change affects the network, contract, or
authentication boundary. Never roll back by copying secrets into source files.

## Troubleshooting

- A production API-key error means `API_KEY` is missing or shorter than 32
  characters.
- A production CORS error means one of the configured origins is a local host;
  replace it with the deployed frontend origin.
- A `503` from `/ready` indicates degraded Horizon or Soroban connectivity;
  inspect the configured URLs and network passphrase before changing retry or
  timeout limits.
- If the agent refuses to start, verify both `AGENT_ENABLED` and the contract's
  responder authorization. Do not enable it with an unverified signing key.
