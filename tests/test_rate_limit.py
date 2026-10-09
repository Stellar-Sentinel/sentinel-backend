from ipaddress import ip_network

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.middleware.rate_limit import ScreeningRateLimitMiddleware


def _client(limit=1, trusted_proxies=()):
    app = FastAPI()

    @app.post("/risk/score")
    def score():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.add_middleware(
        ScreeningRateLimitMiddleware,
        requests=limit,
        window_seconds=60,
        trusted_proxies=trusted_proxies,
    )
    return TestClient(app)


def test_screening_limit_returns_retry_after_and_headers():
    client = _client(limit=1)

    first = client.post("/risk/score")
    limited = client.post("/risk/score")

    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "1"
    assert first.headers["x-ratelimit-remaining"] == "0"
    assert limited.status_code == 429
    assert limited.headers["retry-after"].isdigit()
    assert limited.headers["x-ratelimit-remaining"] == "0"


def test_non_screening_routes_are_not_rate_limited():
    client = _client(limit=1)
    client.post("/risk/score")

    assert client.get("/health").status_code == 200


def test_rate_limit_settings_validate_limits_and_proxy_cidrs():
    settings = Settings(
        screening_rate_limit_requests=5,
        screening_rate_limit_window_seconds=120,
        trusted_proxy_cidrs="127.0.0.1/32, 10.0.0.0/8",
    )

    assert settings.screening_rate_limit_requests == 5
    assert settings.screening_rate_limit_window_seconds == 120
    assert settings.trusted_proxy_networks == [
        ip_network("127.0.0.1/32"),
        ip_network("10.0.0.0/8"),
    ]
    with pytest.raises(ValidationError):
        Settings(screening_rate_limit_requests=0)
    with pytest.raises(ValidationError):
        Settings(trusted_proxy_cidrs="not-a-cidr")


def test_forwarded_for_is_ignored_unless_direct_peer_is_trusted():
    middleware = ScreeningRateLimitMiddleware(
        app=None,
        requests=5,
        window_seconds=60,
        trusted_proxies=[ip_network("10.0.0.0/8")],
    )
    untrusted_scope = {
        "client": ("198.51.100.9", 1234),
        "headers": [(b"x-forwarded-for", b"203.0.113.99")],
    }
    trusted_scope = {
        "client": ("10.0.0.2", 1234),
        "headers": [(b"x-forwarded-for", b"203.0.113.8, 10.0.0.1")],
    }

    assert middleware._client_ip(untrusted_scope) == "198.51.100.9"
    assert middleware._client_ip(trusted_scope) == "203.0.113.8"
