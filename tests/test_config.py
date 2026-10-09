import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_timeout_seconds", 0),
        ("request_timeout_seconds", 61),
        ("operation_scan_limit", 0),
        ("operation_scan_limit", 1_001),
        ("activity_window_days", 0),
        ("activity_window_days", 366),
        ("events_lookback_ledgers", 0),
        ("events_lookback_ledgers", 1_000_001),
    ],
)
def test_rejects_unsafe_runtime_limits(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_accepts_runtime_limit_boundaries():
    settings = Settings(
        request_timeout_seconds=0.1,
        operation_scan_limit=1_000,
        activity_window_days=365,
        events_lookback_ledgers=1_000_000,
    )

    assert settings.request_timeout_seconds == 0.1
    assert settings.operation_scan_limit == 1_000
    assert settings.activity_window_days == 365
    assert settings.events_lookback_ledgers == 1_000_000
