# Event-store schema migration contract

The event store is a persistent SQLite database containing indexed Soroban
events and one ingestion cursor per network/contract scope. Migrations must be
additive, deterministic, and safe to run again during process startup.

## Current schema

`flag_events` is keyed by `(scope, event_id)` and stores the ledger, normalized
event fields, and optional `report_digest`. `ingestion_state` is keyed by
`scope` and stores the opaque RPC cursor. The ledger index is scoped and sorted
descending for stable event pagination.

## Migration invariants

- Existing event rows and ingestion cursors are preserved byte-for-byte.
- A migration may add nullable columns or indexes, but must not reinterpret an
  existing value or change the scope key.
- Startup may run the same migration repeatedly; each step must be guarded by
  schema inspection or `IF NOT EXISTS`.
- Event writes remain idempotent through `(scope, event_id)`.
- A failed migration must fail startup rather than serving partially migrated
  event data.
- Migrations must not call Horizon/Soroban RPC or require signing material.

## Compatibility examples

| Database state | Expected startup behavior |
| --- | --- |
| New database | create both tables and the pagination index |
| Legacy `flag_events` without `report_digest` | add the nullable column; retain all rows |
| Already migrated database | make no data changes and start normally |
| Duplicate event replay | ignore the duplicate row and still update the cursor atomically |
| Invalid event ledger | reject the page transaction; do not advance the cursor |
| Migration/storage error | surface the error and keep the service unavailable |

## Operational constraints

Use a temporary copy of the SQLite file for migration tests. Verify table
columns, indexes, row counts, and cursor values before and after each migration.
Back up `EVENT_STORE_PATH` before deploying a schema change. Any future
destructive or type-changing migration requires an explicit versioned plan and
rollback procedure; it must not be hidden in `CREATE TABLE IF NOT EXISTS`.
