# Event ingestion cursor contract

This document defines the cursor contract used by `sync_flag_events` and the
SQLite event store. It is an internal compatibility contract for the backend
and its Soroban RPC reader; it does not change the contract ABI.

## Cursor scope

An ingestion cursor is scoped by the exact pair
`<network_passphrase>:<contract_id>`. A cursor from another network or contract
must never be reused. An empty cursor means a cold start and causes the reader
to begin at the configured lookback boundary, clamped to the RPC's oldest
retained ledger.

The cursor is opaque. The backend stores and returns it without parsing or
ordering it. RPC implementations may use strings, numbers encoded as strings,
or another provider-defined token.

## Progress invariants

For each sync call:

1. Read the cursor for the current scope.
2. Fetch one bounded page and persist its events and returned cursor together.
3. Continue when the page is empty and the cursor changed.
4. Stop successfully when events are returned, no cursor is returned, or the
   cursor is unchanged.
5. Stop after at most `_MAX_CATCHUP_PAGES` pages (currently 25).

The cursor is persisted after every page, including empty pages. If a later
RPC call fails, a restart resumes from the last persisted cursor rather than
replaying the entire lookback window. Event writes are idempotent by the
`(scope, event_id)` key, so replaying a successfully persisted page is safe.

## Valid and invalid examples

| RPC result | Required behavior |
| --- | --- |
| events plus `cursor: "next"` | persist events and `next`; return for this cycle |
| empty events plus a new cursor | persist the new cursor and continue |
| empty events plus no cursor | persist `null` and stop caught up |
| any page whose cursor equals the input cursor | persist the page and stop to prevent a loop |
| more than 25 advancing empty pages | persist page 25 and stop bounded catch-up |
| RPC error after page 2 | retain page-2 cursor and surface the error to the caller |
| cursor from another scope | ignore it because the store key is scope-qualified |

## Concurrency and recovery constraints

The lifespan task is the current writer and runs one sync cycle at a time. Any
future HTTP-triggered or multi-worker sync entry point must add a per-scope
lock or lease before calling `sync_flag_events`; the SQLite key alone prevents
duplicate rows but does not serialize cursor reads and writes. A lock must be
bounded and released on worker failure.

The reader must keep RPC calls bounded by `REQUEST_TIMEOUT_SECONDS`, retain the
existing retry cap, and never log a cursor, URL query string, signing material,
or upstream exception body. Tests should use fake RPC pages and temporary
SQLite stores only; no live-chain calls are part of this contract.

## Compatibility

The persisted `ingestion_state(scope, cursor)` shape remains unchanged. A
future cursor format migration must be versioned or safely invalidate the
affected scope, and must not reinterpret a token from a different provider.
