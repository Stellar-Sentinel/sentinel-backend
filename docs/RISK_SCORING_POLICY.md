# Risk scoring policy

The screening endpoint returns a deterministic, explainable score. It is a
triage signal, not proof of fraud or financial or compliance advice.

## Inputs and score bands

The live scorer derives signals from the configured account activity window:

| Signal | Default trigger | Points |
| --- | --- | ---: |
| Activity burst | 50 or more operations | 25 |
| Native transfer volume | 10,000 XLM or more | 25 |
| Distinct counterparties | 20 or more | 25 |
| Low account sequence with activity | Sequence 5 or lower | 15 |

Each signal is independently capped by its configured threshold. The score is
the sum of the triggered signal points and is bounded to 100.

The default bands are:

- `0-39`: `low`
- `40-69`: `elevated`
- `70-100`: `high`

`RISK_ELEVATED_SCORE_THRESHOLD` must remain below
`RISK_HIGH_SCORE_THRESHOLD`; configuration validation rejects an invalid band.
The active policy version is returned as `scoring_policy_version` so stored
reports can be interpreted against the policy that produced them.

## Operational guidance

Operators should review the individual `signals`, the activity sample, and the
`as_of` timestamp before taking action. A high score only causes the optional
monitoring agent to consider filing a contract flag when the agent is enabled;
the HTTP screening endpoint never submits an on-chain transaction.

When changing thresholds or weights, update `RISK_POLICY_VERSION`, add or
adjust regression coverage, and record the reason in the deployment notes.
