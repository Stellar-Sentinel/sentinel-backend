# Risk-scoring policy model

The baseline policy is a transparent bounded heuristic, not a trained model or
a compliance decision. It is deterministic for the same input and reports the
component contributions used to produce the score.

## Policy v1.0

`run_pipeline(address, recent_tx_count, recent_tx_volume)` assigns up to 60
points to transaction count and up to 40 points to XLM volume:

```text
transaction_points = min(recent_tx_count / 100, 1) * 60
volume_points      = min(recent_tx_volume / 1,000,000, 1) * 40
score              = round(transaction_points + volume_points)
```

Scores are `low` below 40, `elevated` from 40 through 69, and `high` at 70 or
above. Inputs must be a non-negative integer count and a finite non-negative
number for volume; booleans, negative values, NaN, and infinity are rejected.

The `risk_policy_version` setting identifies the active policy in screening
responses. Caps and weights are deliberately visible so calibration can be
reviewed against labeled Stellar data. The policy does not submit transactions,
claim fraud, or replace human/compliance review.

Tests should cover zero, cap, threshold, malformed, and non-finite inputs using
synthetic values only. No live-chain calls or production account data belong in
policy fixtures.
