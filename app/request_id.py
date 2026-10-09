"""Bounded correlation ID middleware."""
import uuid


_MAX_ID_LENGTH = 64
_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


class RequestIDMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = next(
            (value for key, value in scope.get("headers", []) if key.lower() == b"x-request-id"),
            None,
        )
        try:
            candidate = supplied.decode("ascii") if supplied is not None else ""
        except UnicodeDecodeError:
            candidate = ""
        request_id = (
            candidate
            if 1 <= len(candidate) <= _MAX_ID_LENGTH and all(char in _ALLOWED for char in candidate)
            else uuid.uuid4().hex
        )
        scope["sentinel.request_id"] = request_id

        async def add_request_id(message):
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, add_request_id)
