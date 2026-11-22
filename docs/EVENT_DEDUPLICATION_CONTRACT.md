# Event deduplication contract

Event ingestion is at-least-once and may replay an RPC page after a timeout,
worker restart, or cursor recovery. The event store therefore defines event
identity as the pair `(scope, event_id)`.

## Required behavior

- The first payload stored for an identity is retained; duplicate replay is a
  no-op for event data.
- The cursor update and page inserts are committed atomically.
- A replay in another network/contract scope is a distinct event record.
- A missing ID or ledger rejects the page and leaves the prior cursor intact.
- Event reads remain ordered by descending ledger and event ID within scope.
- Duplicate suppression must not depend on provider ordering or process memory.

The SQLite primary key enforces identity across restarts and workers. Tests use
temporary databases to cover duplicate replay, scope isolation, invalid pages,
and restart recovery. No RPC, live-chain, signing-key, or production-data
dependency is allowed in those tests.
