import httpx

from app import stellar
from app.config import Settings


def test_retry_log_uses_operation_host_and_bounded_fields(monkeypatch, caplog):
    responses = iter(
        [
            httpx.Response(
                503,
                request=httpx.Request(
                    "GET", "https://horizon.example/resource?q=do-not-log-this-value"
                ),
            ),
            httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request("GET", "https://horizon.example/resource"),
            ),
        ]
    )
    monkeypatch.setattr(stellar.time, "sleep", lambda _delay: None)

    with caplog.at_level("WARNING", logger=stellar.logger.name):
        result = stellar._request_with_retries(
            lambda *_args, **_kwargs: next(responses),
            "https://horizon.example/resource?q=do-not-log-this-value",
            Settings(upstream_max_retries=1),
            operation="horizon_get",
        )

    assert result.status_code == 200
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.operation == "horizon_get"
    assert record.upstream == "horizon.example"
    assert record.attempt == 1
    assert record.max_retries == 1
    assert record.reason == "http_503"
    assert "do-not-log-this-value" not in caplog.text


def test_exhaustion_log_reports_attempt_count_without_exception_details(monkeypatch, caplog):
    def fail(*_args, **_kwargs):
        raise httpx.ConnectError("secret endpoint details")

    monkeypatch.setattr(stellar.time, "sleep", lambda _delay: None)

    with caplog.at_level("WARNING", logger=stellar.logger.name):
        try:
            stellar._request_with_retries(
                fail,
                "https://rpc.example/health?q=do-not-log-this-value",
                Settings(upstream_max_retries=1),
                operation="soroban_rpc_post",
            )
        except httpx.ConnectError:
            pass

    assert len(caplog.records) == 2
    assert caplog.records[-1].getMessage().startswith("upstream_retry_exhausted")
    assert caplog.records[-1].attempts == 2
    assert "secret endpoint details" not in caplog.text
    assert "do-not-log-this-value" not in caplog.text
