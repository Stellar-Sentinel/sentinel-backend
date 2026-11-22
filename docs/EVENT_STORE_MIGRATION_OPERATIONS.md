# Event-store migration operations

The event store is a persistent SQLite file at `EVENT_STORE_PATH`. It holds
indexed `flagged`/`flaggedv2` history that may no longer be retrievable from
Soroban RPC once it ages out of provider retention, so schema changes must
evolve that history without rewriting or discarding it.

## Version table

Every store records its applied migrations in `schema_migrations`:

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,   -- 1..N, gap-free
    name TEXT NOT NULL,            -- must match the release's catalog
    applied_at TEXT NOT NULL       -- UTC ISO-8601, informational only
);
```

The current catalog is:

| Version | Name | Change |
| --- | --- | --- |
| 1 | `create_event_tables` | `flag_events`, `ingestion_state`, and the `idx_flag_events_page` paging index. |
| 2 | `add_report_digest` | Nullable `flag_events.report_digest` for `flaggedv2` events. Earlier rows keep `NULL`. |

Example, on a fully migrated file (`sqlite3 events.sqlite3`):

```text
sqlite> SELECT version, name, applied_at FROM schema_migrations;
1|create_event_tables|2026-11-21T14:02:11+00:00
2|add_report_digest|2026-11-21T14:02:11+00:00
```

A file created before version tracking has no `schema_migrations` table. It
is treated as version 0: every migration runs, finds its objects already
present where they exist, and records itself, so legacy, adopted, and new
files all end with the same schema and history.

## When migrations run

Migrations run when the store is first opened in a process. When
`CONTRACT_ID` is set, the application opens the store during startup, before
it accepts requests or starts background ingestion. Without `CONTRACT_ID` the
store is never opened and no file is created.

All pending migrations and their history rows are applied in **one**
`BEGIN IMMEDIATE` transaction:

- Concurrent starters (several workers sharing a volume) serialize on the
  SQLite write lock; the second one sees the first one's history and applies
  nothing.
- If any step fails, the whole upgrade rolls back. The file stays at its
  previous version with its previous schema and rows.
- Reopening an up-to-date file writes nothing and logs nothing.

## Additive migration rules

Migrations live in `MIGRATIONS` in `app/event_store.py` and are composed only
of these step types. There is deliberately no step for destructive changes.

| Step | Effect | Already present |
| --- | --- | --- |
| `CreateTable(name, columns)` | Creates a new table. | Left untouched. |
| `CreateIndex(name, table, columns)` | Creates a new index. | Left untouched. |
| `AddColumn(table, column, declaration)` | Adds a column. | Left untouched. |

Rules for authors:

1. **Append only.** Add the next version number at the end. Never edit,
   reorder, rename, or delete a released migration; stores record its name and
   a mismatch stops startup.
2. **Never destroy evidence.** No `DROP`, no renames, no `UPDATE`/`DELETE` of
   indexed rows, no type changes. Replace a column by adding a new one and
   reading both.
3. **Stay compatible with older writers.** An added column must accept `NULL`
   or declare a constant `DEFAULT`, and must not be `PRIMARY KEY` or `UNIQUE`
   (both enforced when the step is defined). New code must tolerate rows that
   an older release writes without the new column.
4. **Identifiers are fixed code constants.** Table, column, and index names
   must be lowercase identifiers (`[a-z][a-z0-9_]*`); they are validated
   before being placed into DDL.
5. **Test against copies.** Add a test that opens a temporary file in the
   previous shape, migrates it, and compares it with a freshly created one.

The catalog is validated at import time: versions must be `1..N` in order and
names must be unique, otherwise the application fails to import.

## Startup failure behavior

Opening the store either reaches the latest version or raises
`EventStoreSchemaError`. That stops application startup, because a migration
failure must keep the instance unavailable rather than serve partially
initialized storage. Each failure has a fixed `reason` label:

| Reason | Cause | File state |
| --- | --- | --- |
| `newer_schema` | History contains versions newer than this release. | Unchanged. |
| `history_mismatch` | History has gaps, or a known version has a different name. | Unchanged. |
| `migration_failed` | A step raised an SQLite error, for example a legacy table missing a column the step needs. | Rolled back to the previous version. |
| `storage_unavailable` | The file is not a SQLite database, cannot be opened, or another process held the write lock longer than the 10-second busy timeout. | Unchanged. |

Logs carry the reason, the migration label, and version numbers only. They
never include the database path or event payloads:

```text
INFO  event_store_schema_migration migration=create_event_tables status=recorded
INFO  event_store_schema_migration migration=add_report_digest status=applied
ERROR event_store_schema_migration migration=create_event_tables status=failed error=OperationalError
ERROR event_store_schema_check status=rejected reason=newer_schema database_version=3 supported_version=2
WARNING event_store_schema_check status=allowed reason=newer_schema database_version=3 supported_version=2
ERROR event_store_schema_check status=failed reason=storage_unavailable database_version=None supported_version=2
```

`status=applied` means the schema changed; `status=recorded` means the
objects already existed and only the history row was written. If a process
somehow serves `/events/ingestion` while its store cannot be opened, the
endpoint reports `"store": "unavailable"` rather than failing.

## Rollback expectations

Migrations only move forward; there are no down migrations. Two rollback
paths exist:

1. **Restore the backup** taken before the upgrade. This is the default and
   always safe. Ingestion resumes from the backup's cursor and re-indexes
   events still inside the RPC retention window; events that were indexed only
   after the backup and have since aged out of retention are lost.
2. **Run older code over the newer file** by setting
   `EVENT_STORE_ALLOW_NEWER_SCHEMA=true` on the older release. Because every
   migration is additive, the older release keeps reading and writing its own
   columns, and newer columns stay `NULL` on rows it writes. It logs a
   `status=allowed` warning on each startup and does not modify the history.
   Remove the setting once the newer release is redeployed.

Without the setting, an older release refuses a newer file:

```text
EventStoreSchemaError: event store schema version 3 is newer than supported
version 2; set EVENT_STORE_ALLOW_NEWER_SCHEMA=true only for a deliberate code rollback
```

Never delete history rows, drop columns, or hand-edit `schema_migrations` to
force a start. Fix the cause (restore the backup, free the lock, deploy the
matching release) instead.

## Safe rollout

1. Back up the SQLite file and verify the backup can be opened read-only, for
   example `sqlite3 -readonly events.backup.sqlite3 "PRAGMA integrity_check;"`.
2. Deploy the new application with the same `EVENT_STORE_PATH` and
   `EVENT_STORE_ALLOW_NEWER_SCHEMA` unset or `false`.
3. Confirm startup logs show the expected `event_store_schema_migration`
   labels with `status=applied` or `status=recorded`, or nothing at all when
   the file was already current.
4. Verify `schema_migrations` lists every version, and check row counts,
   `(scope, event_id)` uniqueness, ingestion cursors, and indexes before
   returning the instance to traffic.
5. Keep the backup until the deployment has completed a full ingestion cycle.

The persistent volume must have room for the backup and for SQLite's WAL file
during migration. On hosts without a persistent volume (such as a free Render
instance), the file is recreated on every deploy and starts at the latest
version.

## Compatibility

- **HTTP API:** unchanged. `/events` and `/events/ingestion` responses keep
  their shape; no cross-repository contract changes for the frontend.
- **Storage:** adds the `schema_migrations` table. Existing tables, rows, and
  cursors are preserved.
- **Deployment:** a store that cannot be migrated now fails startup instead of
  failing later on requests or in the background indexer.
- **On-chain:** none. No contract ABI change, no signing, and tests make no
  live-chain calls.
