from app.metrics import ServiceMetrics


def test_service_metrics_uses_bounded_route_and_status_labels():
    metrics = ServiceMetrics()
    metrics.record_request("/health", 200, 1.5)
    metrics.record_request("/health?token=secret", 500, 2)

    assert metrics.snapshot() == {
        "requests": {"/health|2xx": 1, "<unmatched>|5xx": 1},
        "latency_ms_total": {"/health": 1.5, "<unmatched>": 2.0},
    }
