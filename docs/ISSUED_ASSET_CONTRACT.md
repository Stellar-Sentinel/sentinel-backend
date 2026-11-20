# Issued assets and trustline contract

Screening responses keep native XLM and issued assets separate. Native balance
uses `{type: "native", code: "XLM"}`; issued assets preserve their Horizon
asset type, code, issuer, and balance string. Credit assets without both a
code and issuer are malformed and are omitted.

`metrics.trustline_count` counts accepted non-native asset balances. Asset
context is descriptive and does not change the risk score. Malformed balances,
non-finite numeric values, and non-object records are ignored without turning a
valid account response into an error.

Consumers should treat issuer identity as part of the asset identity and must
not infer trust from the asset code alone.
