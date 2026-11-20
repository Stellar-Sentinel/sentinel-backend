# Event-store migration operations

The event store is a persistent SQLite file at `EVENT_STORE_PATH`. Schema
migrations run during store initialization before the service begins serving
indexed event data.

## Safe rollout

1. Back up the SQLite file and verify the backup can be opened read-only.
2. Deploy the new application with the same `EVENT_STORE_PATH`.
3. Confirm startup logs contain only the bounded migration label and status;
   they must not contain the filesystem path or event payloads.
4. Verify row counts, `(scope, event_id)` uniqueness, ingestion cursors, and
   expected indexes before returning the instance to traffic.
5. Keep the backup until the deployment has completed a full ingestion cycle.

The current migration adds the nullable `report_digest` column to legacy
`flag_events` tables. It preserves existing rows and cursors and is safe to run
again on an already-migrated database. A migration failure must keep the
instance unavailable rather than serving partially initialized storage.

## Rollback and testing

Rollback means restoring the verified backup or deploying code compatible with
the current schema; do not delete or reinterpret columns during an emergency
rollback. Test migrations against copied temporary databases containing both
legacy and current schemas. Tests must not use live-chain calls, signing keys,
tokens, or production event data.
