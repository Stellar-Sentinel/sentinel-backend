"""Privacy-safe JSON request logging middleware."""
import json
import logging
import time


logger = logging.getLogger("sentinel.request")


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def record_response(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, record_response)
        finally:
            route = scope.get("route")
            route_template = getattr(route, "path", None) or "<unmatched>"
            record = {
                "method": scope.get("method", ""),
                "route": route_template,
                "status": status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "request_id": scope.get("sentinel.request_id"),
            }
            logger.info(json.dumps(record, separators=(",", ":")))
