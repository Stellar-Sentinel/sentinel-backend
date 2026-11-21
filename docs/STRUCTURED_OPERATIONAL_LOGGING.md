# Structured operational logging contract

This document defines the privacy-preserving log contract for the Stellar
Sentinel backend. It is an operational interface: dashboards, alert rules,
and retention policies may depend on the fields and meanings below.

## Scope and invariants

The application emits one JSON object for each completed HTTP request and
selected background-agent lifecycle events. Every record is UTF-8 JSON on one
line and has these required fields:

| Field | Type | Invariant |
| --- | --- | --- |
| `schema_version` | string | Semantic contract version, initially `1`. |
| `event` | string | Stable event name, for example `http.request.completed`. |
| `severity` | string | One of `INFO`, `WARNING`, or `ERROR`. |
| `request_id` | string or null | The bounded `X-Request-ID` value, or null for non-HTTP events. |
| `occurred_at` | string | UTC timestamp in RFC 3339 format when available. |

HTTP request records additionally contain `method`, `route`, `status`,
`status_class`, `outcome`, and non-negative `latency_ms`. `route` is always a
route template or the literal `<unmatched>`; it is never the raw request path.
`status_class` is one of `1xx`, `2xx`, `3xx`, `4xx`, or `5xx`. `outcome` is
`success` for status codes below 400 and `failure` otherwise.

The event name and field names are compatibility-sensitive. Adding optional
fields is backward compatible; changing field meaning, removing a required
field, or changing redaction rules requires a new schema version and a
migration note.

## Privacy and failure rules

Logs must not contain raw paths, query strings, request or response bodies,
headers, bearer/API keys, signing keys, account secrets, or upstream exception
messages. Error records may include an exception *type* or a bounded internal
reason code, but not exception text supplied by an upstream service. Values
derived from user input must be bounded and allowlisted before logging.

If a request fails before a response starts, the record uses status `500` and
`outcome: "failure"`. Logging itself must not change the response or mask the
original exception. If serialization fails, the logger should fall back to a
minimal safe record rather than serializing arbitrary scope data.

## Examples

Successful request:

```json
{"schema_version":"1","event":"http.request.completed","severity":"INFO","request_id":"req-42","occurred_at":"2026-01-01T12:00:00Z","method":"GET","route":"/health","status":200,"status_class":"2xx","outcome":"success","latency_ms":1.23}
```

Unmatched request (the account-like path and query are intentionally absent):

```json
{"schema_version":"1","event":"http.request.completed","severity":"WARNING","request_id":"generated-id","occurred_at":"2026-01-01T12:00:01Z","method":"GET","route":"<unmatched>","status":404,"status_class":"4xx","outcome":"failure","latency_ms":0.91}
```

## Retention and deployment guidance

Ship logs to the deployment platform's structured-log sink. Retain normal
request records for the shortest period that supports reliability analysis;
use access-controlled, shorter-lived retention for warning/error records.
Redaction is a producer responsibility, so downstream retention does not
make it safe to log secrets. Dashboards should aggregate by `event`, `route`,
`status_class`, and bounded reason fields rather than request IDs or paths.

## Compatibility checklist

Before changing this contract, add valid, invalid, boundary, and compatibility
examples to tests or fixtures; confirm that request IDs remain bounded; test
404 and 5xx paths; and review the complete diff for credentials, raw URLs,
headers, bodies, and exception details. This change has no API, storage,
deployment, or on-chain ABI migration by itself.
