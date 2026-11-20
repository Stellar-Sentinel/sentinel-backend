# Monitoring-agent cooldown and duplicate flags

This runbook describes the safeguards around repeated `flag_anomaly_v2`
submissions. It applies only to the optional backend monitoring agent; it does
not change the Sentinel contract ABI or the dashboard.

## Configuration

The agent is disabled unless `AGENT_ENABLED=true`, `AGENT_SECRET_KEY` is set,
and `CONTRACT_ID` identifies the deployed contract. The signing key must be an
already-authorized responder. Never place a key in this file, a commit, or a
log line.

| Variable | Default | Bound and purpose |
| --- | --- | --- |
| `AGENT_POLL_INTERVAL_SECONDS` | `300` | 30 seconds to 24 hours between cycles |
| `AGENT_SCAN_CANDIDATE_LIMIT` | `20` | 1 to 200 recent operations examined per cycle |
| `AGENT_FLAG_COOLDOWN_SECONDS` | `3600` | 60 seconds to 7 days per subject |
| `REQUEST_TIMEOUT_SECONDS` | `8.0` | 0.1 to 60 seconds for upstream reads |

The candidate window is newest-first and deduplicated by source account. The
agent account itself is skipped. A candidate below the configured high-risk
threshold is not submitted.

## Decision and duplicate behavior

For each cycle, the agent records bounded counters for candidates seen and
scored, successful submissions, fixed skip reasons (`self`, `cooldown`, and
`below_threshold`), and fixed failure reasons (`scoring` and `submission`).
Counters never include account addresses, request payloads, signing material,
or exception text. A failure for one candidate does not abort the cycle.

The per-address cooldown is held in process memory and starts only after a
flag transaction is confirmed successful. A failed or timed-out submission is
therefore eligible for retry on the next cycle. A successful submission for a
subject is skipped until `AGENT_FLAG_COOLDOWN_SECONDS` elapses.

### Example

With `AGENT_SCAN_CANDIDATE_LIMIT=20` and a one-hour cooldown, a cycle can
produce a snapshot like this (illustrative only):

```json
{
  "candidates_seen": 20,
  "candidates_scored": 17,
  "flags_submitted": 2,
  "skipped": {"self": 1, "cooldown": 4, "below_threshold": 10},
  "failures": {"scoring": 1, "submission": 2}
}
```

These counters are diagnostics for the most recent cycle and are safe to
publish to an operator-facing metrics adapter. They are not a durable audit
log and should not be interpreted as proof that the network has been fully
scanned.

## Restart, recovery, and rollback

Cooldown state is intentionally in memory. A process restart clears it, so a
successful subject may be eligible again after restart. Operators should use a
stable deployment and conservative cooldown values when duplicate submissions
would be costly. Durable cooldown state requires a separately reviewed storage
design and is not enabled by this service.

If the agent is repeatedly failing, set `AGENT_ENABLED=false` and restart the
service. This stops new submissions while leaving read-only screening and
event indexing available. Check the fixed counters and application logs for
whether failures are in scoring or submission, then verify Horizon/RPC health,
contract authorization, network passphrase, and account funding. Do not copy
secret values into tickets or logs.

To roll back a deployment, redeploy the previous application image and keep
the same network and contract settings. Because cooldown state is process
local, treat a rollback or restart as a cooldown reset and monitor the first
cycle before re-enabling automatic submissions.

## Coordination and compatibility

The backend calls the existing `flag_anomaly_v2` contract function and keeps
the report digest format unchanged. Contract and frontend work must continue
to use the existing interface and event fields. Event ingestion remains the
source of indexed confirmation; it is separate from the agent's in-memory
cooldown map. For persistent, multi-replica operation, run one agent worker or
introduce a separately reviewed shared coordination mechanism.

See [Monitoring-agent candidate selection](MONITORING_CANDIDATE_SELECTION.md),
the [contracts interface](https://github.com/Stellar-Sentinel/sentinel-contracts#contract-interface),
and the configuration table in the main [README](../README.md).
