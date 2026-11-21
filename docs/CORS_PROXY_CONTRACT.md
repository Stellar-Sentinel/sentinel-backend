# CORS and trusted-proxy contract

`CORS_ORIGINS` is an explicit comma-separated allowlist. Origins must include
scheme and host, and the service must not use `*` with credentials. The
allowlist controls browser access only; it is not authentication.

`TRUSTED_PROXY_CIDRS` identifies exact proxy networks whose forwarded client
address may be trusted. With an empty allowlist, the middleware uses the direct
peer address and ignores `X-Forwarded-For`. A proxy must overwrite or append
forwarded headers according to the deployment contract.

Do not accept arbitrary forwarded client addresses, log raw authorization
headers, or treat CORS success as permission to call protected routes.
