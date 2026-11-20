# Report digest contract

`flag_anomaly_v2` binds an on-chain flag to a canonical off-chain screening
report through `report_digest`. The digest is the SHA-256 hash of compact,
UTF-8 JSON with lexicographically sorted keys.

The canonical payload contains exactly:

- `address`
- `score`
- `risk_level`
- `threshold`
- `scoring_policy_version`
- `as_of`

Fields such as signals, metrics, assets, and activity samples are deliberately
excluded so operational enrichment does not invalidate an already-submitted
flag. Values must retain their JSON types; changing a score, threshold, policy
version, timestamp, or address changes the digest.

Consumers should recompute the digest from the stored report, compare the full
32-byte value, and treat a missing or malformed digest as unverified. Never
display the digest as proof that an account is malicious: it proves only that
the listed canonical fields were bound together when the flag was submitted.
