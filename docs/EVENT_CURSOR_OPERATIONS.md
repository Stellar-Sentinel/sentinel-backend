# Event ingestion cursor operations

The event indexer persists one Soroban RPC continuation cursor per
`network_passphrase:contract_id` scope in `ingestion_state`. The scope prevents
switching networks or contracts from reusing an unrelated position.

## Catch-up behavior

On startup and on each ingest interval, the service resumes from the stored
cursor. It continues through empty RPC chunks when the provider returns a new
cursor, because an empty chunk is not proof that the retained range is caught
up. The walk stops when the provider returns no continuation cursor, repeats a
cursor, or reaches the bounded catch-up page limit.

Event rows are inserted idempotently by `(scope, event_id)`. A page and its
cursor are written in the same SQLite transaction, so a malformed event or a
failed transaction cannot advance the resume point without its data.

## Recovery and maintenance

Back up `EVENT_STORE_PATH` together with deployment configuration. Restoring a
database resumes from the restored cursor; deleting it starts from the
configured retained lookback window and cannot recover events already pruned by
the RPC provider. If RPC is unavailable, `/events` serves indexed history and
marks the source as stale.

For multiple replicas, run a single ingestion worker against a supported shared
SQLite volume. Do not treat the SQLite file as a network database. When
changing cursor or event schema, ship an explicit migration and test restart,
empty-page, duplicate-page, and malformed-page behavior.
