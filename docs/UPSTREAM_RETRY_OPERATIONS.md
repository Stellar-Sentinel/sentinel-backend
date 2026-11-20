# Operating upstream retries

Transient Horizon and Soroban RPC failures are expected during provider
maintenance and network congestion. The service retries only read operations,
with a configured maximum of three retries and a capped delay. Operators should
use the bounded retry diagnostics to distinguish transport, transient HTTP,
and exhausted failures without inspecting upstream response bodies.

## Configuration guidance

`UPSTREAM_MAX_RETRIES` controls attempts after the initial request;
`UPSTREAM_RETRY_BACKOFF_SECONDS` controls exponential fallback delay; and
`UPSTREAM_RETRY_AFTER_CAP_SECONDS` caps provider-directed delays. Keep
`REQUEST_TIMEOUT_SECONDS` finite and ensure the product of timeout, retry count,
and capped waits fits the deployment's request budget.

Retries do not apply to client errors or JSON-RPC application errors. Those
failures should be investigated at the caller/provider boundary rather than
hidden with repeated requests.

## Monitoring and incident response

Alert on sustained exhausted-retry events by operation and upstream host. Do
not aggregate or export full URLs, query strings, response bodies, exception
messages, tokens, or signing material. When a provider is degraded, `/ready`
may return `503` while `/live` remains available; this is expected and should
not trigger a process restart by itself.
