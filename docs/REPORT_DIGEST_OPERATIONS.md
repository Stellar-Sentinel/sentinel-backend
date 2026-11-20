# Report digest operations

Report digests are verification material for `flag_anomaly_v2`, not a risk
decision by themselves. Store the exact canonical report fields alongside the
screening result so an operator can recompute the digest later.

## Submission and verification

Before submission, confirm the report contains `address`, `score`, `risk_level`,
`threshold`, `scoring_policy_version`, and `as_of`. Serialize those fields as
compact sorted-key JSON and hash the UTF-8 bytes with SHA-256. After a
transaction is confirmed, store the transaction hash and the 32-byte digest.

During review, recompute the digest from the stored fields and compare the full
value. A missing digest, malformed length, field mismatch, or unconfirmed
transaction is an unverified result. Do not silently substitute a newer report
or include extra fields in the canonical payload.

## Retention and change management

Retain reports for at least the same period as the alert investigation record.
If the scoring schema changes, bump `RISK_POLICY_VERSION`; old reports remain
verifiable because the policy version is part of their canonical payload.
Changes to canonicalization require coordinated contract, API, and verifier
updates plus fixtures covering old and new payloads.
