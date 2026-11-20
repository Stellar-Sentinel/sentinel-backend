# Request correlation and security-header contract

Every HTTP response receives a bounded `X-Request-ID`. A valid client value
containing only ASCII letters, digits, `.`, `_`, or `-` is preserved up to 64
characters; invalid or missing values are replaced with a fresh UUID. The
response contains exactly the effective ID used for correlation.

Responses also include privacy and browser-boundary headers: `X-Content-Type-
Options: nosniff`, `X-Frame-Options: DENY`, and
`Referrer-Policy: no-referrer`. Middleware must apply them to successful,
error, and unmatched routes alike.

Request logs may include the route template, status, latency, and effective
request ID, but never raw paths, query strings, headers, bodies, credentials,
or upstream exception text. Request IDs are correlation handles, not
authentication or authorization tokens.
