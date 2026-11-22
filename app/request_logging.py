"""Privacy-safe JSON request logging middleware."""
import json
import logging
import time
from datetime import datetime, timezone

from app.metrics import service_metrics

logger = logging.getLogger("sentinel.request")


def _status_class(status_code):
    return f"{status_code // 100}xx" if 100 <= status_code <= 599 else "unknown"


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
            status_class = _status_class(status_code)
            record = {
                "schema_version": "1",
                "event": "http.request.completed",
                "severity": "INFO" if status_code < 400 else "WARNING",
                "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "method": scope.get("method", ""),
                "route": route_template,
                "status": status_code,
                "status_class": status_class,
                "outcome": "success" if status_code < 400 else "failure",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "request_id": scope.get("sentinel.request_id"),
            }
            service_metrics.record_request(route_template, status_code, record["latency_ms"])
            logger.info(json.dumps(record, separators=(",", ":")))
