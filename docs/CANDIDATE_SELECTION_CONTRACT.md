# Monitoring candidate-selection contract

Candidate selection is the boundary between the network operation window and
the optional monitoring agent's risk scoring. It must be deterministic for a
given Horizon response and bounded by configuration.

## Interface and invariants

`recent_network_accounts(settings, limit)` performs one read of the network
operations endpoint with `order=desc`, `include_failed=false`, and the supplied
limit. It returns valid `source_account` strings in first-seen order, with
duplicates removed.

Invalid records, missing fields, empty strings, and non-string accounts are
skipped. The agent account is excluded before scoring, and the configured
candidate limit bounds the amount of upstream data and downstream work.

## Boundary cases

- Empty or missing `_embedded.records` produces an empty candidate list.
- Duplicate accounts retain the newest occurrence because the provider result
  is newest-first.
- A malformed record cannot abort selection of later records.
- A limit of `N` is passed to the upstream request; selection does not fetch a
  second page to compensate for malformed entries.
- Scoring, cooldown, and submission decisions happen after selection and are
  isolated per candidate.

## Compatibility and privacy

This contract depends on Horizon operation source-account fields only; it does
not change the Stellar contract ABI or public API. Tests use synthetic account
strings and fake payloads. Logs and metrics must use bounded counts and fixed
skip labels rather than raw operation payloads or credentials.
