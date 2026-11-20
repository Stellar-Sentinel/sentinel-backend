# Upstream retry and backoff contract

Read-only Horizon and Soroban RPC requests use a bounded retry policy. The
policy is shared by both clients so a transient provider failure cannot create
unbounded work or turn an application error into a duplicate write.

## Retry matrix

Retry only transport failures and HTTP `408`, `425`, `429`, `500`, `502`, `503`,
and `504`. Do not retry client errors, successful responses, malformed JSON, or
JSON-RPC application errors returned with HTTP 200.

`UPSTREAM_MAX_RETRIES` is capped at 3. The initial delay is
`UPSTREAM_RETRY_BACKOFF_SECONDS` and doubles per attempt. A numeric or
HTTP-date `Retry-After` value is honored for transient HTTP responses, subject
to `UPSTREAM_RETRY_AFTER_CAP_SECONDS` (maximum 5 seconds). Invalid headers fall
back to exponential backoff.

## Failure and privacy behavior

After the final attempt, the original transport/status path is mapped to the
existing service error taxonomy. Retry diagnostics may include a fixed
operation label, upstream host, attempt count, status/reason, and capped delay;
they must not include URLs, query strings, response bodies, credentials, or
signing material.

Tests use fake HTTP responses and transport exceptions, patch sleep, and assert
the exact call count and delay. They make no live-chain requests.
