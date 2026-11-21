# Event ingestion cursor management

The indexer copies `flagged` and `flaggedv2` contract events from Soroban RPC
into the local SQLite store, resuming from a persisted RPC cursor. This page
covers the cursor's scope, how far one sync can go, how overlapping syncs
behave, and what happens when RPC fails or returns data that can't be used.

## Cursor scope

One cursor is stored per scope, in `ingestion_state(scope, cursor)`. The scope
is `"<NETWORK_PASSPHRASE>:<CONTRACT_ID>"`:

```text
Test SDF Network ; September 2015:CBVFEUGUZ3P7A3F6B3SZKFJRTSANHYZDGEOFRCRLQL3FG6QCDO7MBJD7
```

The same scope is part of event identity `(scope, event_id)`. When either
value changes, the new scope has no cursor, so ingestion starts from the
lookback window (`EVENTS_LOOKBACK_LEDGERS`, clamped to the provider's
oldest retained ledger). The old scope keeps its cursor and history. Changing
`SOROBAN_RPC_URL` alone does not change the scope, because Soroban cursors
encode chain position and stay valid across providers on the same network.

The storage schema is unchanged by this work; no migration is required.

## One sync

`sync_flag_events` performs a bounded walk:

1. Read the scope's cursor (or start from the lookback window).
2. Request one `getEvents` page of `EVENT_INGEST_PAGE_SIZE` events.
3. Validate the continuation cursor, then insert the page and advance the
   cursor in one SQLite transaction.
4. Repeat until one of the stop conditions below is reached.

| Stop reason | Meaning | Cursor after |
| --- | --- | --- |
| `caught_up` | RPC returned no continuation cursor. | Unchanged |
| `cursor_stalled` | RPC returned the cursor it was given. | Unchanged |
| `partial_page` | A non-empty page shorter than the page size. | Advanced |
| `page_budget_exhausted` | `EVENT_INGEST_MAX_PAGES` pages were read. | Advanced; next sync continues |
| `sync_in_progress` | Another sync for this scope is already running in this process. | Untouched; nothing fetched |
| `cursor_conflict` | Another process advanced the cursor first. | The other process's value |

Empty pages and full pages keep the walk going. Soroban RPC scans a bounded
ledger range per call, so a cold start can return many empty pages before the
first match, and a backlog returns full pages. A short non-empty page ends
the walk; the next poll picks up from there.

### Work bound

One sync reads at most `EVENT_INGEST_MAX_PAGES × EVENT_INGEST_PAGE_SIZE`
events (default 25 × 100 = 2,500) and makes at most
`EVENT_INGEST_MAX_PAGES + 1` RPC calls (one `getHealth` on a cold start). With
retries, each call can take up to
`(UPSTREAM_MAX_RETRIES + 1) × REQUEST_TIMEOUT_SECONDS` plus capped retry
waits. `GET /events` runs a sync inline, so keep this product within your
gateway's request timeout. Lowering `EVENT_INGEST_MAX_PAGES` shortens the
worst case at the cost of a slower cold start.

## Forward progress

A continuation cursor is checked before the store sees it. These cases
reject the page as an upstream error (`502`); neither the events nor the
cursor are written:

- The cursor is not a 1–128 character string of letters, digits, `.`, `_`,
  `:`, or `-`.
- The cursor is the standard Soroban `<19-digit TOID>-<10-digit index>` form
  and sorts before the current cursor.
- The cursor repeats one already seen in the same sync (`A → B → A`).
- The event list is not a list of objects, or an event has no usable ID or
  ledger.

Cursors in other formats are treated as opaque: they are checked for shape
and repetition only, never ordered.

## Concurrent syncs

The background indexer and `GET /events` can both start a sync.

- **Within one process**, a lock per store file and scope lets only one sync
  run at a time. A second caller does not wait: it returns
  `sync_in_progress`, and `GET /events` serves the indexed records with
  `ingestion_status: "current"`.
- **Across processes** sharing one SQLite file, every cursor write is a
  compare-and-set against the cursor the sync started from. If another
  worker moved the cursor meanwhile, the slower worker's page is discarded
  and it stops with `cursor_conflict`. Event inserts are idempotent, so the
  only cost is a repeated RPC read.

The recommendation to run a single ingestion worker still stands. The
compare-and-set protects correctness, not efficiency.

## Recovery after RPC errors

| Failure | Behavior |
| --- | --- |
| Timeout or connection failure (`503`) | Already committed pages stay. The sync raises; the next one resumes from the last committed cursor. |
| HTTP or JSON-RPC error (`502`) | Same as above. |
| Malformed page or cursor (`502`) | That page is discarded and the cursor stays put. |
| RPC rejects the persisted cursor | Handled by `EVENT_INGEST_CURSOR_RECOVERY` (below). |

`GET /events` reports any `502`/`503` from a sync as
`source.ingestion_status: "stale"` and still returns the indexed history.

The background indexer polls every `EVENT_INGEST_INTERVAL_SECONDS`. After
consecutive failures it waits `interval × 2^failures`, capped at
`EVENT_INGEST_MAX_BACKOFF_SECONDS` and never shorter than the interval. One
success restores the normal interval. With the defaults (30 s interval, 300 s
cap) the waits are 60, 120, 240, 300, 300, ... seconds.

### Rejected cursor

After downtime longer than the provider's retention window, Soroban RPC
typically rejects the stored cursor with JSON-RPC `-32600` (invalid request)
or `-32602` (invalid params). The request filters never change, so only these
codes, and only on a request that carried a persisted cursor, count as a
rejection.

- `halt` (default): the cursor is kept and the sync fails. `/events` stays
  `stale` until an operator acts. The warning logs only the code and the
  policy, never the provider's message or the cursor:

  ```text
  event_ingestion_cursor_rejected rpc_error_code=-32600 recovery=halt
  ```

- `reset`: the cursor is deleted (guarded by compare-and-set) and the same
  sync restarts from the lookback window. Indexed history is kept and
  replayed events are deduplicated. Events pruned upstream during the outage
  are not recoverable. The reset happens at most once per sync; if the fresh
  start is rejected too, the error is raised.

Manual recovery under `halt` is either switching to `reset` for one restart,
or deleting the scope's row:

```sql
DELETE FROM ingestion_state WHERE scope = 'Test SDF Network ; September 2015:<CONTRACT_ID>';
```

## Configuration example

A deployment that values a fast cold start and unattended recovery:

```dotenv
EVENT_INGEST_INTERVAL_SECONDS=15
EVENT_INGEST_PAGE_SIZE=200
EVENT_INGEST_MAX_PAGES=50
EVENT_INGEST_MAX_BACKOFF_SECONDS=600
EVENT_INGEST_CURSOR_RECOVERY=reset
```

Out-of-range values (page size outside 1–1,000, max pages outside 1–100,
backoff ≤ 0, or a recovery value other than `halt`/`reset`) fail at startup.

## Compatibility

- `GET /events` keeps its response shape, pagination cursor, and the two
  `ingestion_status` values `current` and `stale`. Consumers such as
  sentinel-frontend need no change.
- JSON-RPC application errors are still HTTP `502` with the detail
  `Soroban RPC returned an error`. The numeric code is only used inside the
  service.
- `EventStore.save_page` keeps its signature; `expected_cursor` is an
  optional keyword argument.
- No contract ABI, signing, or transaction-submission behavior changes.
