# Risk scoring policy

The screening endpoint returns a deterministic, explainable score. It is a
triage signal, not proof of fraud, a trained prediction, or financial or
compliance advice.

## Inputs and score bands

The live scorer derives signals from native Stellar account activity observed
within the configured activity window:

| Signal | Default trigger | Points |
| --- | --- | ---: |
| Activity burst | 50 or more operations | 25 |
| Native transfer volume | 10,000 XLM or more | 25 |
| Distinct counterparties | 20 or more | 25 |
| Low account sequence with activity | Sequence 5 or lower | 15 |

Each trigger and point value is configurable. The score sums triggered signals
and is capped at 100. The default bands are `0-39` (`low`), `40-69`
(`elevated`), and `70-100` (`high`). `RISK_ELEVATED_SCORE_THRESHOLD` must stay
below `RISK_HIGH_SCORE_THRESHOLD`; configuration validation rejects an invalid
pair.

`RISK_POLICY_VERSION` identifies the active policy and is returned as
`scoring_policy_version`, so saved reports can be interpreted against the
policy that produced them.

## Operational guidance

Review the individual `signals`, activity sample, metrics, and `as_of` timestamp
before taking action. A high score only makes the optional monitoring agent
consider filing a contract flag when the agent is enabled. The HTTP screening
endpoint itself does not submit an on-chain transaction.

When changing thresholds or weights, update `RISK_POLICY_VERSION`, add or
adjust regression coverage, and record the reason in deployment notes. Tests
should use synthetic data only; do not use live-chain calls or production
account data in policy fixtures.
