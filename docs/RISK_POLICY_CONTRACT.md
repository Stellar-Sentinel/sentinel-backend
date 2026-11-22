# Risk scoring policy contract

The scorer is a deterministic, explainable heuristic over the configured
activity window. Each signal contributes a bounded configured point value; the
sum is capped at 100. `RISK_ELEVATED_SCORE_THRESHOLD` must be lower than
`RISK_HIGH_SCORE_THRESHOLD`.

The response exposes the score, risk band, threshold, policy version, signals,
metrics, activity sample, source, and timestamp. A policy change must bump
`RISK_POLICY_VERSION` and preserve the meaning of historical reports. The
screening endpoint never submits an on-chain action.

Scores are triage signals, not proof of fraud or financial advice. Operators
must review signal explanations and data freshness before acting.
