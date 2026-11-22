# Screening response cache

Successful screening responses may be cached in-process to avoid repeating the
same bounded Horizon reads during a short burst. The cache is an optimization,
not a source of truth.

## Key and lifetime

Entries are keyed by network, Horizon URL, account, and active risk-policy
settings. This prevents a response from one network, endpoint, account, or
policy revision being reused for another. Entries expire after
`SCREENING_CACHE_TTL_SECONDS` and the ordered cache is capped at
`SCREENING_CACHE_MAX_ENTRIES` with oldest-entry eviction.

Set the TTL to `0` to disable caching. Each application worker has its own
cache; deployments requiring shared freshness must coordinate at the gateway
or disable the local cache.

## Safety and invalidation

Only validated response dictionaries are copied into and out of the cache.
Cache access is protected by a process-local lock. `clear_screening_cache()` is
available to tests and lifecycle code. Cache misses and expired entries perform
the normal upstream request; errors are never converted into cached successes.

The cache must not contain tokens, signing material, private user data, or raw
upstream exception details. Tests use synthetic accounts and monkeypatched
upstream calls, with no live-chain dependency.
