# Monitoring-agent transaction lifecycle

The monitoring agent is an optional, single-process worker. It has no signing
authority unless both `AGENT_ENABLED=true` and `AGENT_SECRET_KEY` are
configured, and the corresponding public key is already authorized on the
configured contract.

## States and invariants

Each candidate moves through these observable states:

1. `observed`: the account appeared in the bounded Horizon operation sample.
2. `screened`: the account was scored with the same scorer used by the API.
3. `eligible`: the score met the configured high-risk threshold and the account
   was not the agent itself or on cooldown.
4. `submitted`: a `flag_anomaly_v2` transaction was accepted as pending.
5. `confirmed`: the transaction reached Soroban `SUCCESS`.
6. `failed`: screening, simulation, submission, or confirmation failed.

The worker must never submit for its own public key, must not submit below the
threshold, and must not stop the poll loop because one candidate fails. A
successful submission starts the per-account cooldown. The cooldown is an
in-memory safety bound and resets when the process restarts.

Only `confirmed` is a completed on-chain action. `pending`, timeout, and
failed states must be treated as indeterminate or unsuccessful by operators;
the agent does not retry a failed transaction inside the same candidate pass.

## Deployment checks

Before enabling the worker, verify the contract ID, network passphrase,
authorized responder, RPC reachability, and a bounded scan/cooldown policy.
Keep agent logs free of secret keys and inspect transaction hashes through the
network's explorer or RPC before investigating an alert as confirmed.
