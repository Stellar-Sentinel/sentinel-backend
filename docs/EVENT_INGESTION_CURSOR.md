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
| `time_budget_exhausted` | `EVENT_INGEST_SYNC_BUDGET_SECONDS` elapsed before the next page. | Advanced; next sync continues |
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
waits.

`EVENT_INGEST_SYNC_BUDGET_SECONDS` (default 60) caps wall-clock time on top
of the page budget. It is checked before each page after the first, so:

- the first page always runs, and a budget that is too small cannot freeze
  ingestion;
- the overrun is at most one page's worst case. With defaults that is
  `60 + 3 × 8 s + capped retry waits`, about 90 seconds.

`GET /events` runs a sync inline, so keep the budget plus one page's worst
case within your gateway's request timeout. Lowering
`EVENT_INGEST_MAX_PAGES` or the time budget shortens the worst case at the
cost of a slower cold start.

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
| Malformed `getHealth` ledger window on a cold start (`502`) | No `getEvents` call is made and nothing is written. This used to surface as an unhandled `500`. |
| RPC rejects the persisted cursor | Handled by `EVENT_INGEST_CURSOR_RECOVERY` (below). |
| SQLite error | Propagates unchanged (`GET /events` still returns `500`); labelled `store_error` in telemetry. |

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
EVENT_INGEST_SYNC_BUDGET_SECONDS=20
EVENT_INGEST_MAX_BACKOFF_SECONDS=600
EVENT_INGEST_CURSOR_RECOVERY=reset
```

Out-of-range values (page size outside 1–1,000, max pages outside 1–100,
a sync budget ≤ 0 or above 3,600 seconds, backoff ≤ 0, or a recovery value
other than `halt`/`reset`) fail at startup.

## Telemetry and diagnostics

Every sync is recorded, whether it succeeds, fails, or is skipped. Each sync
produces one log line and one counter update, and both use labels from fixed
lists (`INGESTION_STOP_REASONS` and `INGESTION_FAILURES` in `app/metrics.py`).
Any other value is counted as `other`, so neither logs nor counters can grow
an unbounded label set.

### Failure labels

| Label | Cause | HTTP status |
| --- | --- | --- |
| `rpc_timeout` | Connect, read, write, or pool timeout after retries. | `503` |
| `rpc_unavailable` | Other transport failure (refused, DNS, TLS). | `503` |
| `rpc_http_error` | Non-2xx HTTP response after retries. | `502` |
| `rpc_invalid_response` | Body is not JSON or not an object, `result` is not an object, or the `getHealth` ledger window is malformed. | `502` |
| `rpc_error` | JSON-RPC application error that is not a cursor rejection, including a rejected fresh start after a `reset`. | `502` |
| `cursor_rejected` | RPC rejected a request that carried a cursor, and recovery did not apply: the policy is `halt`, or this sync already reset once. | `502` |
| `invalid_cursor` | The continuation cursor failed the shape check. | `502` |
| `cursor_regressed` | The continuation cursor moved backwards or looped. | `502` |
| `malformed_page` | The event list is malformed, or an event has no ID or ledger. | `502` |
| `store_error` | SQLite raised an error. | — |
| `upstream_error` | Any other `HTTPException` raised by a sync. | varies |
| `internal_error` | Anything else. Only this label also logs the exception class name. | — |

### Log lines

All lines use the `sentinel.events` logger except `event_ingestion_backoff`,
which comes from `app.main`. Structured fields are attached through `extra`.

```text
event_ingestion_sync stop_reason=page_budget_exhausted pages=25 events=0 duration_ms=4120.5
event_ingestion_sync_failed failure=rpc_timeout status_code=503 pages=2 events=7 duration_ms=8013.2
event_ingestion_cursor_rejected rpc_error_code=-32600 recovery=halt
event_ingestion_backoff failure=rpc_timeout consecutive_failures=3 next_attempt_seconds=240.0
```

- A successful sync logs at `INFO` when it stored events, reset the cursor,
  hit the page or time budget, or lost a cursor conflict. Otherwise it logs
  at `DEBUG`, so an idle indexer stays quiet.
- A failure logs at `WARNING`. `pages` and `events` count work committed
  before the failure. That work is durable and the next sync resumes after
  it.
- `sync_in_progress` logs at `DEBUG`. It is counted, but it does not reset
  the failure streak or update `last_success_at`, because no work was done.

### Diagnostics endpoint

`GET /events/ingestion` returns the process's counters plus the persisted
cursor's position. It never calls Soroban RPC and never starts a sync, so it
is cheap to poll and stays available during an RPC outage.

```json
{
  "enabled": true,
  "state": "failing",
  "store": "ok",
  "cursor": {"present": true, "ledger": 123456},
  "consecutive_failures": 2,
  "last_sync": {"result": "failed", "stop_reason": null, "failure": "rpc_timeout",
                "pages": 1, "events": 0, "duration_ms": 8012.4, "at": "2026-01-01T12:05:00+00:00"},
  "last_success_at": "2026-01-01T12:00:00+00:00",
  "last_failure": {"failure": "rpc_timeout", "at": "2026-01-01T12:05:00+00:00"},
  "totals": {"syncs": {"caught_up": 10, "...": 0}, "failures": {"rpc_timeout": 2, "...": 0},
             "pages": 40, "events": 112, "cursor_resets": 0},
  "limits": {"page_size": 100, "max_pages": 25, "sync_budget_seconds": 60.0,
             "interval_seconds": 30, "max_backoff_seconds": 300, "cursor_recovery": "halt"}
}
```

`totals.syncs` and `totals.failures` always list every label, including
`other`, with zeros where nothing has happened.

`state` takes one of these values:

| `state` | Meaning |
| --- | --- |
| `disabled` | `CONTRACT_ID` is unset. The store is not opened. |
| `unknown` | No sync has completed in this process yet, for example just after a restart. |
| `ok` | The most recent sync finished with a stop reason. |
| `failing` | The most recent sync raised. See `last_failure.failure`. |

`store` is `unavailable` when the SQLite cursor cannot be read. The endpoint
still answers `200` with the in-memory counters.

Counters are kept per process and reset on restart. With several workers,
each one reports only its own syncs; the cursor ledger comes from the shared
store and is the same for all of them.

### Operational scenarios

- **Provider outage.** `state` becomes `failing` with `rpc_timeout` or
  `rpc_unavailable`, and `consecutive_failures` climbs. Backoff lines show
  the growing wait. `/ready` also reports `soroban_rpc: unavailable`. Action:
  none until the provider recovers; the next success resets the streak.
- **Cursor aged out under `halt`.** `failing` with `cursor_rejected`, plus
  `event_ingestion_cursor_rejected` warnings, while `/ready` stays `ok`.
  This combination (RPC healthy, ingestion failing) is the signal to follow
  the manual recovery steps above.
- **Cold-start backlog.** Repeated `page_budget_exhausted` or
  `time_budget_exhausted` with `state: ok`, and `cursor.ledger` rising
  between polls. This is expected. Raise the budgets only if catch-up is too
  slow.
- **Stuck progress.** `state: ok` but `cursor.ledger` stays flat while the
  network's `latest_ledger` (from `/network/status`) advances, and the syncs
  are mostly `cursor_stalled`. Check the provider's event retention and
  filters.

Suggested alerts: `consecutive_failures >= 3`; any `cursor_rejected`;
`last_success_at` older than `10 × EVENT_INGEST_INTERVAL_SECONDS`; any
`invalid_cursor`, `cursor_regressed`, or `malformed_page`. The last group
means the provider is sending data the indexer refuses to store.

## Security notes

- **Secrets.** Ingestion holds no signing key and never signs anything. The
  diagnostics endpoint and logs carry no URLs, credentials, request bodies,
  or environment values. The endpoint is covered by `API_KEY` when one is
  configured. Only `/health`, the docs routes, and `/openapi.json` are
  exempt.
- **Trust boundary.** Soroban RPC output is untrusted. Provider error
  messages, response bodies, and exception text are never logged or
  returned. Telemetry carries only fixed labels, counts, durations, the
  numeric JSON-RPC error code, and a cursor's ledger number. The raw cursor
  stays out of logs and responses. Only standard `<TOID>-<index>` cursors
  are decoded to a ledger; opaque provider cursors report `ledger: null`.
- **Degraded dependencies.** With RPC down, the endpoint still answers from
  memory and SQLite. With SQLite unreadable, it reports
  `store: unavailable` instead of failing. `GET /events` keeps serving
  indexed history marked `stale` on any RPC failure. A malformed health
  response now also produces `stale` instead of a `500`.

## Compatibility

- `GET /events` keeps its response shape, pagination cursor, and the two
  `ingestion_status` values `current` and `stale`. Consumers such as
  sentinel-frontend need no change.
- `GET /events/ingestion` is new and additive. It is an operator contract,
  not a dashboard one. Its labels are the fixed lists above; adding a label
  is a backwards-compatible change, while renaming or removing one is a
  breaking change for alert rules.
- JSON-RPC application errors are still HTTP `502` with the detail
  `Soroban RPC returned an error`. The numeric code is only used inside the
  service. A halted cursor rejection is still a `SorobanRpcError` with the
  original code.
- Data errors keep their exact `502` details. They are now raised as
  `UpstreamDataError`, a subclass of `HTTPException`.
- `EventStore.save_page` keeps its signature; `expected_cursor` is an
  optional keyword argument.
- `EVENT_INGEST_SYNC_BUDGET_SECONDS` is new. Its 60-second default only
  ends syncs early that would previously have run longer, and those resume
  on the next poll.
- The storage schema is unchanged; no migration is required. No contract
  ABI, signing, or transaction-submission behavior changes.
