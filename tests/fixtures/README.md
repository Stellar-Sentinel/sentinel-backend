# Synthetic risk fixtures

`risk_policy_cases.json` contains deterministic, anonymized calibration cases
for low, elevated, and high outcomes. Account identifiers are intentionally
omitted; tests supply synthetic keys when building Horizon-shaped payloads.

Each case records the sequence context, operation shape, policy overrides, and
expected score classification. Keep fixtures bounded and free of signing
seeds, access tokens, private user data, or live-chain dependencies.
