# Screening rate-limit contract

Only `POST /risk/score` is rate-limited by the process-local sliding window.
The configured request count and window are positive bounded values. Successful
responses include `X-RateLimit-Limit` and `X-RateLimit-Remaining`.

When the window is exhausted, the service returns 429 with a generic body,
`Retry-After`, `X-RateLimit-Limit`, and a zero remaining count. Health,
readiness, events, network status, and CORS preflight are not consumed by the
screening quota.

Client identity uses the direct peer unless that peer is inside
`TRUSTED_PROXY_CIDRS`; only then is a validated forwarded chain considered.
The limiter is process-local and must be enforced at a shared gateway for
multi-worker or multi-replica deployments.
