# Screening response cache

Successful screening responses may be cached in-process to avoid repeating the
same bounded Horizon reads during a short burst. The cache is an optimization,
not a source of truth.

## Key and lifetime

Entries are keyed by network passphrase, Horizon URL, account address, and the
complete active settings snapshot. This prevents a response from one network,
endpoint, account, or policy configuration being reused for another. Entries
expire after `SCREENING_CACHE_TTL_SECONDS` (15 seconds by default), and the
ordered cache evicts the least-recently-used entry when it exceeds
`SCREENING_CACHE_MAX_ENTRIES` (256 by default). Set the TTL to `0` to disable
caching.

Each application worker has its own cache. Cache access uses a process-local
lock, and `clear_screening_cache()` is available to tests and lifecycle code.
Validated responses are deep-copied into and out of the cache; upstream errors
are never converted into cached successes. Do not cache tokens, signing
material, private user data, or raw upstream exception details.

## Operations and tests

Use a short TTL when rapid policy changes or near-real-time activity matter.
Increase the maximum only after measuring process memory. Deploy updated
settings with policy changes and bump `RISK_POLICY_VERSION` so cached responses
and reports remain interpretable. Tests should use synthetic accounts and
mocked upstream calls; they must not depend on live-chain access.
