# Risk calibration fixture contract

Calibration fixtures are synthetic Horizon-shaped inputs paired with explicit
expected screening outcomes. They are used to review threshold changes without
calling the network or relying on production accounts.

Each case must include a stable name, sequence context, operation shape, policy
overrides when needed, and expected `score`, `risk_level`, and
`threshold_exceeded` values. Cases should cover low, elevated, and high outcomes
plus zero, threshold, cap, malformed, and non-finite boundaries.

Fixtures must omit account identifiers, signing seeds, access tokens, private
user data, and raw production payloads. Tests supply synthetic account strings
and monkeypatch upstream reads. A policy-version change should update the
fixture expectations deliberately, with the version recorded in the test
configuration rather than hidden in a default.
