"""Small bounded in-process service metrics used by operational tooling."""
import math
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock

MAX_ROUTE_LABEL_LENGTH = 128


def bounded_route_label(route: str) -> str:
    if isinstance(route, str) and route.startswith("/") and "?" not in route and len(route) <= MAX_ROUTE_LABEL_LENGTH:
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


service_metrics = ServiceMetrics()


# Every label the ingestion telemetry can emit. Anything else collapses to
# "other" so a caller bug can never turn a counter into an unbounded map.
INGESTION_STOP_REASONS = (
    "caught_up",
    "cursor_stalled",
    "partial_page",
    "page_budget_exhausted",
    "time_budget_exhausted",
    "sync_in_progress",
    "cursor_conflict",
)
INGESTION_FAILURES = (
    "rpc_timeout",
    "rpc_unavailable",
    "rpc_http_error",
    "rpc_invalid_response",
    "rpc_error",
    "cursor_rejected",
    "invalid_cursor",
    "cursor_regressed",
    "malformed_page",
    "store_error",
    "upstream_error",
    "internal_error",
)
_OTHER = "other"


def _bounded(label: str, allowed: tuple[str, ...]) -> str:
    return label if label in allowed else _OTHER


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IngestionMetrics:
    """Process-local counters and last-outcome state for event ingestion.

    Counters reset on restart and are not shared between worker processes;
    the persisted cursor in SQLite remains the cross-process source of truth.
    """

    def __init__(self):
        self._lock = Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._outcomes = dict.fromkeys(INGESTION_STOP_REASONS + (_OTHER,), 0)
            self._failures = dict.fromkeys(INGESTION_FAILURES + (_OTHER,), 0)
            self._pages = 0
            self._events = 0
            self._cursor_resets = 0
            self._consecutive_failures = 0
            self._last_sync = None
            self._last_success_at = None
            self._last_failure = None

    def record_sync(self, stop_reason: str, pages: int, events: int,
                    duration_ms: float, cursor_reset: bool = False) -> None:
        stop_reason = _bounded(stop_reason, INGESTION_STOP_REASONS)
        with self._lock:
            self._outcomes[stop_reason] += 1
            # A skipped sync did no work and says nothing about upstream health,
            # so it must not mask a failure streak or refresh last_success_at.
            if stop_reason == "sync_in_progress":
                return
            now = _utc_now()
            self._add_progress(pages, events, cursor_reset)
            self._consecutive_failures = 0
            self._last_success_at = now
            self._last_sync = self._sync_record("ok", stop_reason, None, pages, events, duration_ms, now)

    def record_failure(self, failure: str, pages: int, events: int,
                       duration_ms: float, cursor_reset: bool = False) -> None:
        failure = _bounded(failure, INGESTION_FAILURES)
        with self._lock:
            now = _utc_now()
            self._failures[failure] += 1
            # Pages committed before the failure are durable, so they count.
            self._add_progress(pages, events, cursor_reset)
            self._consecutive_failures += 1
            self._last_failure = {"failure": failure, "at": now}
            self._last_sync = self._sync_record("failed", None, failure, pages, events, duration_ms, now)

    def _add_progress(self, pages: int, events: int, cursor_reset: bool) -> None:
        self._pages += max(0, int(pages))
        self._events += max(0, int(events))
        self._cursor_resets += int(bool(cursor_reset))

    @staticmethod
    def _sync_record(result, stop_reason, failure, pages, events, duration_ms, at) -> dict:
        return {
            "result": result,
            "stop_reason": stop_reason,
            "failure": failure,
            "pages": max(0, int(pages)),
            "events": max(0, int(events)),
            "duration_ms": round(max(0.0, float(duration_ms)), 2),
            "at": at,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "consecutive_failures": self._consecutive_failures,
                "last_sync": dict(self._last_sync) if self._last_sync else None,
                "last_success_at": self._last_success_at,
                "last_failure": dict(self._last_failure) if self._last_failure else None,
                "totals": {
                    "syncs": dict(self._outcomes),
                    "failures": dict(self._failures),
                    "pages": self._pages,
                    "events": self._events,
                    "cursor_resets": self._cursor_resets,
                },
            }


ingestion_metrics = IngestionMetrics()
