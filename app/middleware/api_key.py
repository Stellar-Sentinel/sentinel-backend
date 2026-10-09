"""Optional bearer-key protection for the public API routes."""
import json
import secrets


class ApiKeyMiddleware:
    def __init__(self, app, api_key: str):
        if len(api_key) < 32:
            raise ValueError("API_KEY must contain at least 32 characters")
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.api_key:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if (scope.get("method") == "OPTIONS"
                or path in {"/health", "/docs", "/redoc", "/openapi.json"}):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, candidate = authorization.partition(" ")
        valid = (
            separator == " "
            and scheme.lower() == "bearer"
            and secrets.compare_digest(candidate, self.api_key)
        )
        if valid:
            await self.app(scope, receive, send)
            return

        body = json.dumps({"detail": "A valid bearer API key is required"}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
