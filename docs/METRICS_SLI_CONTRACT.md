# Metrics and service-level indicators

The service should measure behavior without recording account payloads or
credentials. The minimum SLI set is:

- request success rate, grouped by route template and status class;
- request latency, using the `latency_ms` field from structured request logs;
- readiness success rate for Horizon and Soroban RPC independently;
- event-ingestion freshness and stale-source duration;
- monitoring-agent cycle count, candidate count, submission count, and failure
  count.

## Boundaries

Metrics labels must remain bounded. Use route templates, status classes,
dependency names, and fixed event names; never use raw paths, account
addresses, transaction hashes, request IDs, or exception messages as labels.
Counters are monotonic for the process lifetime and histograms use a stable
unit. A restart resets in-memory observations and must be visible in the
deployment record.

## SLO interpretation

Set availability and latency targets per deployment after collecting a baseline.
Treat readiness failures and stale ingestion as separate indicators: a healthy
HTTP process does not imply a current event index. Alert on sustained breach,
not on one transient upstream retry.
