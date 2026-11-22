import math

from app.metrics import ServiceMetrics


def test_service_metrics_uses_bounded_route_and_status_labels():
    metrics = ServiceMetrics()
    metrics.record_request("/health", 200, 1.5)
    metrics.record_request("/health?token=secret", 500, 2)

    assert metrics.snapshot() == {
        "requests": {"/health|2xx": 1, "<unmatched>|5xx": 1},
        "latency_ms_total": {"/health": 1.5, "<unmatched>": 2.0},
    }


def test_metrics_reject_unbounded_labels_and_nonfinite_latency():
    metrics = ServiceMetrics()
    metrics.record_request("/health?token=secret", 200, math.nan)
    metrics.record_request("/" + "x" * 200, 200, math.inf)

    snapshot = metrics.snapshot()
    assert snapshot["requests"] == {"<unmatched>|2xx": 2}
    assert snapshot["latency_ms_total"] == {"<unmatched>": 0.0}
