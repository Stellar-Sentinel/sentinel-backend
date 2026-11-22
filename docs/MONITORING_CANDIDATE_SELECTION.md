# Monitoring-agent candidate selection

The optional monitoring agent makes one bounded Horizon read per cycle and
uses the returned operations as its candidate window. The reader requests
newest-first operations with `include_failed=false`, so failed operations do
not become candidates and newer activity is considered first.

## Selection rules

- Preserve the provider's newest-first order.
- Keep the first occurrence of each valid `source_account` only.
- Skip malformed records and missing, empty, or non-string source accounts.
- Never score the configured agent account itself.
- Apply the configured candidate limit before scoring; no additional upstream
  reads are made to expand the window.
- Skip candidates on their per-account cooldown or below the risk threshold.

The candidate list is an input window, not a claim that it represents all
network activity. The configured limit and Horizon request timeout bound work
per cycle. Scoring and submission failures are isolated per candidate so one
bad account cannot abort the rest of the cycle.

## Diagnostics and privacy

Candidate selection must not log full operation payloads, credentials, query
strings, or signing material. A future operational metric should use bounded
counts such as `candidates_seen`, `candidates_skipped`, and
`candidates_scored`, with skip reasons from a fixed vocabulary.

Fixtures for this contract use synthetic public keys and fake Horizon payloads;
they must not make live-chain calls or contain production account data.
