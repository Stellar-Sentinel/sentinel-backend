# Issued asset and trustline operations

The `/risk/score` response exposes account balances as descriptive context in
`assets`; it does not use issued-asset balances or trustline count as an
automatic risk decision. Operators should preserve the full asset identity:

- Native XLM is reported as `{type: "native", code: "XLM"}` and is not a
  trustline.
- Issued assets include their Horizon `asset_type`, `asset_code`, and
  `asset_issuer`. The issuer is part of the identity; never merge assets by
  code alone.
- `metrics.trustline_count` counts valid credit-asset balances only.
- Liquidity-pool shares remain visible in `assets`, but are not trustlines.

When reviewing an account, confirm the original `balance` string and the asset
identity before displaying it to an operator. Malformed or non-finite Horizon
balance records are skipped. Treat skipped records as an upstream-data quality
signal and investigate the source response rather than guessing a balance.

Operational checks:

1. Compare the account response with the configured Horizon network and record
   the observation time from `source.observed_at`.
2. Validate that every issued balance has both a code and issuer before using
   it in downstream reports.
3. Keep native XLM totals separate from issued-asset amounts; issued amounts
   must never be added to `transfer_volume_xlm`.
4. If the account changes between reviews, treat the new balance and
   trustline count as a fresh observation rather than an historical correction.
