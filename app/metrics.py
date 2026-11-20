"""Bounded in-process service indicators."""
import math
from collections import defaultdict
from threading import Lock


MAX_ROUTE_LABEL_LENGTH = 128


def bounded_route_label(route: str) -> str:
    if (
        isinstance(route, str)
        and route.startswith("/")
        and "?" not in route
        and len(route) <= MAX_ROUTE_LABEL_LENGTH
    ):
        return route
    return "<unmatched>"


class ServiceMetrics:
    def __init__(self):
        self._lock = Lock()
        self._requests = defaultdict(int)
        self._latency_ms = defaultdict(float)

    def record_request(self, route: str, status: int, latency_ms: float) -> None:
        route = bounded_route_label(route)
        status_class = f"{int(status) // 100}xx"
        latency = float(latency_ms)
        if not math.isfinite(latency):
            latency = 0.0
        with self._lock:
            self._requests[(route, status_class)] += 1
            self._latency_ms[route] += max(0.0, latency)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "requests": {
                    f"{route}|{status_class}": count
                    for (route, status_class), count in self._requests.items()
                },
                "latency_ms_total": dict(self._latency_ms),
            }
