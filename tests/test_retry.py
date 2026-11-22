"""Deterministic coverage for the bounded upstream retry policy."""

import httpx
import pytest

from app import stellar
from app.config import Settings


def response(status: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        headers=headers,
        request=httpx.Request("GET", "https://upstream.example/resource"),
    )


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(status, monkeypatch):
    calls = []
    delays = []
    settings = Settings(upstream_max_retries=1, upstream_retry_backoff_seconds=0.25)

    def request(*_args, **_kwargs):
        calls.append(1)
        return response(status)

    monkeypatch.setattr(stellar.time, "sleep", delays.append)
    result = stellar._request_with_retries(request, "https://upstream.example/resource", settings)

    assert result.status_code == status
    assert len(calls) == 2
    assert delays == [0.25]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_statuses_are_not_retried(status, monkeypatch):
    calls = []

    def request(*_args, **_kwargs):
        calls.append(1)
        return response(status)

    monkeypatch.setattr(stellar.time, "sleep", pytest.fail)
    result = stellar._request_with_retries(
        request,
        "https://upstream.example/resource",
        Settings(upstream_max_retries=3),
    )

    assert result.status_code == status
    assert len(calls) == 1


def test_retry_after_is_honored_but_capped(monkeypatch):
    delays = []
    responses = iter([response(429, headers={"Retry-After": "30"}), response(200)])

    monkeypatch.setattr(stellar.time, "sleep", delays.append)
    result = stellar._request_with_retries(
        lambda *_args, **_kwargs: next(responses),
        "https://upstream.example/resource",
        Settings(upstream_max_retries=1, upstream_retry_after_cap_seconds=2),
    )

    assert result.status_code == 200
    assert delays == [2]


def test_malformed_retry_after_uses_exponential_backoff(monkeypatch):
    delays = []
    responses = iter([response(503, headers={"Retry-After": "later"}), response(200)])

    monkeypatch.setattr(stellar.time, "sleep", delays.append)
    stellar._request_with_retries(
        lambda *_args, **_kwargs: next(responses),
        "https://upstream.example/resource",
        Settings(
            upstream_max_retries=1,
            upstream_retry_backoff_seconds=0.4,
            upstream_retry_after_cap_seconds=5,
        ),
    )

    assert delays == [0.4]


def test_transport_failures_retry_then_recover(monkeypatch):
    calls = []
    delays = []

    def request(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("temporary outage")
        return response(200)

    monkeypatch.setattr(stellar.time, "sleep", delays.append)
    result = stellar._request_with_retries(
        request,
        "https://upstream.example/resource",
        Settings(upstream_max_retries=1, upstream_retry_backoff_seconds=0.3),
    )

    assert result.status_code == 200
    assert delays == [0.3]


def test_final_transport_failure_is_propagated(monkeypatch):
    calls = []

    def request(*_args, **_kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("upstream timed out")

    with pytest.raises(httpx.ReadTimeout):
        stellar._request_with_retries(
            request,
            "https://upstream.example/resource",
            Settings(upstream_max_retries=2),
        )

    assert len(calls) == 3


def test_rpc_application_errors_are_not_retried(monkeypatch):
    calls = []
    payload = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600}}

    def post(*_args, **_kwargs):
        calls.append(1)
        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request("POST", "https://rpc.example"),
        )

    monkeypatch.setattr(stellar.httpx, "post", post)
    with pytest.raises(stellar.HTTPException) as caught:
        stellar._rpc("getHealth", {}, Settings(upstream_max_retries=3))

    assert caught.value.status_code == 502
    assert len(calls) == 1
