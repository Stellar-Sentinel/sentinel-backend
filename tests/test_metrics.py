import math

from app.metrics import ServiceMetrics


def test_metrics_reject_unbounded_labels_and_nonfinite_latency():
    metrics = ServiceMetrics()
    metrics.record_request("/health?token=secret", 200, math.nan)
    metrics.record_request("/" + "x" * 200, 200, math.inf)

    snapshot = metrics.snapshot()
    assert snapshot["requests"] == {"<unmatched>|2xx": 2}
    assert snapshot["latency_ms_total"] == {"<unmatched>": 0.0}
