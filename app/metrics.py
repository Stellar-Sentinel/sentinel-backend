"""Small bounded in-process service metrics used by operational tooling."""
from collections import defaultdict
from threading import Lock


class ServiceMetrics:
    def __init__(self):
        self._lock = Lock()
        self._requests = defaultdict(int)
        self._latency_ms = defaultdict(float)

    def record_request(self, route: str, status: int, latency_ms: float) -> None:
        route = route if route.startswith("/") and "?" not in route else "<unmatched>"
        status_class = f"{int(status) // 100}xx"
        with self._lock:
            self._requests[(route, status_class)] += 1
            self._latency_ms[route] += max(0.0, float(latency_ms))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "requests": {
                    f"{route}|{status_class}": count
                    for (route, status_class), count in self._requests.items()
                },
                "latency_ms_total": dict(self._latency_ms),
            }


service_metrics = ServiceMetrics()
