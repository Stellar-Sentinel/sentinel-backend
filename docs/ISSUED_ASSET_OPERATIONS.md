# Issued asset operations

Review `assets` and `metrics.trustline_count` as descriptive account context,
not as a risk decision. Confirm each issued balance has an asset type, code,
issuer, and original balance string before presenting it to operators.

Malformed balances are skipped and should be tracked through test fixtures or
upstream-data monitoring. Do not coalesce assets by code alone: the issuer is
part of the identity. Native XLM remains a separate balance and does not count
as a trustline.
