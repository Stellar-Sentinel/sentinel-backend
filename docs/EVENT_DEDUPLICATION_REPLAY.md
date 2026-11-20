# Event deduplication and replay

Soroban event ingestion is at-least-once: a provider page may be replayed after
a process restart or an RPC failure. The event store treats a `(scope,
event_id)` pair as the stable identity of an indexed event.

## Replay contract

- Replaying the same event in the same network/contract scope is a no-op.
- The first stored payload wins; a later replay cannot overwrite event data.
- The cursor is advanced in the same SQLite transaction as the page write.
- Replaying an event in a different scope is valid and stores a separate row.
- A page missing an event ID or ledger is rejected and does not advance the
  cursor.

## Recovery examples

If page 1 is stored and the worker crashes before acknowledging the RPC call,
the next run may receive page 1 again. `INSERT OR IGNORE` preserves the existing
row, then the cursor update leaves the worker ready for page 2. If the process
crashes during the transaction, SQLite rolls back both the event rows and the
cursor, so the page can be safely replayed.

Tests use temporary SQLite files to cover duplicate replay, scope isolation,
restart recovery, and invalid-page rejection. They never call Horizon or
Soroban RPC and contain no production data or signing material.
