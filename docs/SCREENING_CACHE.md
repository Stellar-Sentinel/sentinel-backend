# Screening response cache

The account screening path keeps a small in-process cache of successful
responses. It reduces duplicate upstream reads while keeping the cache local
to the application worker.

## Key and lifecycle

An entry is keyed by the network passphrase, Horizon URL, account address, and
the complete active settings snapshot. A change to a scoring threshold,
activity window, or upstream endpoint therefore cannot reuse a response made
under different policy inputs.

Entries expire after `SCREENING_CACHE_TTL_SECONDS` (15 seconds by default) and
the cache evicts the least-recently-used entry after
`SCREENING_CACHE_MAX_ENTRIES` (256 by default). Set the TTL to `0` to disable
caching. The service returns independent copies, so a caller cannot mutate the
stored response for later callers.

The cache is process-local. Each worker or replica has its own entries, and a
shared cache is intentionally not required for correctness. Upstream failures
are never stored as successful screening responses.

## Operations

Use a short TTL when rapid policy changes or near-real-time activity matter.
Increase the maximum only after measuring process memory. When changing the
policy, bump `RISK_POLICY_VERSION` and deploy the new settings together with
the service so cached responses and the returned policy version remain easy to
interpret.
