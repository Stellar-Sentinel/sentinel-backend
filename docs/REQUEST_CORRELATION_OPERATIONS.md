# Request correlation and security-header operations

Every HTTP response carries an `X-Request-ID`. The middleware accepts only
ASCII letters, digits, dots, underscores, and hyphens, with a maximum length
of 64 characters. Missing or invalid values are replaced by a generated UUID
hex string, so log labels remain bounded and safe to index.

Request-completion logs use the route template, status class, outcome, latency,
and the selected request ID. They deliberately omit raw paths, query strings,
headers, bodies, and upstream exception details. A missing route is recorded
as `<unmatched>` rather than copying attacker-controlled input into a log
label.

Responses include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
and `Referrer-Policy: no-referrer`. Screening responses also set
`Cache-Control: no-store` because they can contain account-specific results.
Health and readiness routes remain available during upstream degradation, while
the readiness response reports dependency status without exposing exception
details.

## Operational checks

1. Pass a stable request ID from the client when tracing a retry, for example
   `X-Request-ID: screening_2026-01-01T120000Z`; do not put secrets or account
   data in it.
2. Propagate the response ID to support tooling and correlate it with the
   structured completion log.
3. Treat a 503 readiness response as a dependency signal, not as permission to
   retry without a bounded client policy. Keep retry counts and backoff in the
   caller, and never retry a write blindly.
4. Alert on elevated 4xx/5xx status classes and latency by route template. Do
   not create metrics labels from request paths, query values, or IDs.

These headers and logging rules are API compatibility requirements for the
frontend and deployment log collector. They do not change the Stellar contract
ABI or make heuristic screening a compliance or fraud determination.
