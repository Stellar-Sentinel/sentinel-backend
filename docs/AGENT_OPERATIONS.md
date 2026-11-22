# Monitoring-agent operations

The monitoring agent is disabled by default. Enable it only after the
deployment has a contract ID, a dedicated `AGENT_SECRET_KEY`, and an
authorized responder on the same network and contract. The API's screening
endpoint remains read-only; only the optional worker submits
`flag_anomaly_v2`.

## Preflight

Before enabling the worker, verify:

- `AGENT_ENABLED=true` and `AGENT_SECRET_KEY` are present only in the secret
  manager or process environment;
- `CONTRACT_ID`, `SOROBAN_RPC_URL`, and `NETWORK_PASSPHRASE` identify the
  intended deployment;
- the public key derived from the secret is authorized by the contract;
- `AGENT_SCAN_CANDIDATE_LIMIT`, `AGENT_POLL_INTERVAL_SECONDS`, and
  `AGENT_FLAG_COOLDOWN_SECONDS` are bounded for the deployment;
- logs and crash reports do not include the secret key.

## Runtime behavior

Each cycle samples recent network operations, removes duplicate accounts,
scores candidates, and submits only for accounts at or above the high-risk
threshold. The agent skips its own account and accounts in cooldown. A scoring
or transaction failure is logged and skipped so one candidate cannot stop the
poll loop. Cooldown begins after a successful submission, not after an
eligibility decision.

Monitor logs for candidate counts, scoring failures, simulation failures,
submission rejections, and confirmation timeouts. A pending or timed-out
transaction is not a confirmed flag; verify its hash through Soroban before
escalating. Keep one active worker per event stream unless duplicate
submissions are explicitly coordinated.
