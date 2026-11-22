# Metrics and SLI operations

Collect the service indicators from structured request events and the
application's dependency and worker logs. The primary dashboards should show
request success and latency by route template, readiness by dependency, event
ingestion freshness, and monitoring-agent cycle outcomes.

Keep labels bounded to route templates, status classes, dependency names, and
fixed event names. Do not label metrics with request IDs, addresses, hashes,
raw paths, or exception text. Prefer counters and aggregate latency totals in
the application process; export them through a collector that can tolerate
restarts.

## Alerting

Page on a sustained availability breach or a readiness dependency failure.
Create a lower-severity alert for rising latency, stale event ingestion, or
agent submission failures. Correlate with the versioned request-log schema and
deployment timestamp, and avoid treating one transient upstream retry as an
incident.

Document target windows and thresholds beside the dashboards. Revisit them
after a baseline period and whenever upstream retry, cache, or polling limits
change.
