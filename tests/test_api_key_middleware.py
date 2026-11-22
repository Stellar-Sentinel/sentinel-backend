import asyncio

import pytest

from app.middleware.api_key import ApiKeyMiddleware


def _run(scope, api_key, app_headers=None):
    sent = []

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": app_headers or []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(ApiKeyMiddleware(app, api_key)(scope, receive, send))
    return sent


def test_bearer_key_is_case_insensitive_and_protected_routes_reject_wrong_key():
    key = "secret-key" * 4
    scope = {"type": "http", "method": "GET", "path": "/risk/score", "headers": [(b"authorization", f"bEaReR {key}".encode())]}
    assert _run(scope, key)[0]["status"] == 200

    wrong = {**scope, "headers": [(b"authorization", b"Bearer wrong") ]}
    response = _run(wrong, "secret-key" * 4)
    assert response[0]["status"] == 401
    assert dict(response[0]["headers"])[b"www-authenticate"] == b"Bearer"


def test_health_and_options_remain_public_and_short_keys_are_rejected():
    with pytest.raises(ValueError, match="32"):
        ApiKeyMiddleware(lambda *_args: None, "too-short")

    for method, path in (("GET", "/health"), ("OPTIONS", "/risk/score")):
        scope = {"type": "http", "method": method, "path": path, "headers": []}
        assert _run(scope, "secret-key" * 4)[0]["status"] == 200
