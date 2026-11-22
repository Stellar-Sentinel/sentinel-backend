# Screening rate-limit operations

Only `POST /risk/score` is protected by the in-process sliding-window limiter.
Health, readiness, event, and network-status reads remain available so
operators can distinguish an overloaded screening path from a down service.

The limiter returns HTTP 429 with `Retry-After`, `X-RateLimit-Limit`, and
`X-RateLimit-Remaining`. The retry value is bounded by the configured window;
clients should honor it and use capped exponential backoff rather than retrying
every failed request immediately. A caller must not blindly retry a write.

The accounting key is the direct peer address unless the peer belongs to a
configured `TRUSTED_PROXY_CIDRS` range. For a trusted proxy, the middleware
walks the forwarded chain until the first untrusted hop. Configure the gateway
to overwrite the chain and keep proxy ranges exact; otherwise clients can
spoof one another's limiter keys.

The limiter is process-local. With multiple workers or replicas, enforce a
shared limit at the edge and keep the application limiter as a local safety
net. During an incident, reduce the configured request limit or apply a
gateway limit, then restore it gradually after upstream latency and error
rates recover. No signing material, account data, or raw forwarded headers are
stored in the limiter state or emitted as metrics labels.

Frontend clients should surface 429 as a temporary capacity response, preserve
the request ID for support correlation, and avoid retrying malformed requests.
The deployment log collector should monitor bounded route/status labels and
latency, not query strings or raw client paths.
