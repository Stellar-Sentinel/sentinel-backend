# Structured logging operations

HTTP request logs are newline-delimited JSON records with a stable
`event` name and `schema_version`. The payload includes method, route
template, status, latency, and the sanitized request ID. It intentionally
omits raw paths, query strings, headers, bodies, and upstream exception text.

## Collection

Ship logs to a collector that preserves one JSON object per line. Parse by
`event` and `schema_version`, not by message position. Alert on sustained 5xx
rates, latency percentiles, and missing request IDs; use the request ID to
correlate an individual response with application logs without logging user
inputs.

## Privacy and retention

Treat route templates and request IDs as operational metadata. Do not add
account addresses, API keys, tokens, payloads, or upstream URLs to request
logs. Apply the deployment's retention and access controls, and update
consumers when the schema version changes.
