from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app import stellar
from app.config import Settings
from app.main import app

client = TestClient(app)
ADDRESS = "G" + "A" * 55


def test_health_and_cors():
    assert client.get("/health").json() == {"status": "ok"}
    response = client.options("/risk/score", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_score_uses_horizon_data_and_returns_bounded_explainable_signals(monkeypatch):
    records = [{"created_at": datetime.now(timezone.utc).isoformat(), "type": "payment",
                "source_account": ADDRESS, "to": "G" + "B" * 55, "asset_type": "native", "amount": "12000"}]
    def fake_get(url, params=None, settings=None):
        if url.endswith("/accounts/" + ADDRESS):
            return {"sequence": "10", "balances": [{"asset_type": "native", "balance": "500"}]}
        return {"_embedded": {"records": records}}
    monkeypatch.setattr(stellar, "_get", fake_get)
    response = client.post("/risk/score", json={"address": ADDRESS})
    assert response.status_code == 200
    payload = response.json()
    assert payload["score"] == 25
    assert payload["threshold_exceeded"] is False
    assert payload["metrics"]["transfer_volume_xlm"] == 12000
    assert payload["signals"][0]["source"] == "Stellar Horizon account operations"
    assert client.post("/risk/score", json={"address": ADDRESS, "recent_tx_count": 10}).status_code == 422


def test_events_requires_contract_id():
    monkeypatch_settings = Settings(contract_id="")
    try:
        stellar.list_flag_events(20, settings=monkeypatch_settings)
        assert False, "expected configuration error"
    except HTTPException as exc:
        assert exc.status_code == 503
        assert "CONTRACT_ID" in exc.detail


def test_events_filter_and_decode(monkeypatch):
    settings = Settings(contract_id="C" + "A" * 55)
    calls = []
    def fake_rpc(method, params, selected):
        calls.append((method, params))
        if method == "getHealth":
            return {"latestLedger": 100000, "oldestLedger": 80000}
        return {"events": [{"id": "id1", "ledger": 99999, "ledgerClosedAt": "now", "contractId": selected.contract_id,
                            "topic": [{"symbol": "flagged"}, {"address": {"accountId": "agent"}},
                                      {"address": {"accountId": "subject"}}], "value": {"u32": 82}}], "cursor": "opaque"}
    monkeypatch.setattr(stellar, "_rpc", fake_rpc)
    result = stellar.list_flag_events(15, settings=settings)
    assert result["events"][0]["agent"] == "agent"
    assert result["events"][0]["subject"] == "subject"
    assert result["events"][0]["score"] == 82
    assert calls[1][1]["filters"][0]["contractIds"] == [settings.contract_id]
    assert calls[1][1]["startLedger"] == 80000
    assert result["next_cursor"] == "opaque"


def test_network_status_includes_rpc_retention(monkeypatch):
    monkeypatch.setattr(stellar, "_rpc", lambda method, params, settings: {
        "status": "healthy", "latestLedger": 100, "oldestLedger": 50,
        "ledgerRetentionWindow": 120960,
    })
    result = stellar.network_status(Settings())
    assert result["ledger_retention_window"] == 120960
    assert result["oldest_ledger"] == 50


def test_transient_upstream_status_retries_and_honors_retry_after(monkeypatch):
    request = httpx.Request("GET", "https://horizon.example")
    responses = [
        httpx.Response(429, headers={"Retry-After": "0.4"}, request=request),
        httpx.Response(200, json={"ok": True}, request=request),
    ]
    delays = []

    def fake_get(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(stellar.httpx, "get", fake_get)
    monkeypatch.setattr(stellar.time, "sleep", delays.append)

    result = stellar._get("https://horizon.example", settings=Settings())

    assert result == {"ok": True}
    assert delays == [0.4]


def test_permanent_upstream_status_is_not_retried(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(stellar.httpx, "get", fake_get)

    try:
        stellar._get("https://horizon.example/accounts/missing", settings=Settings())
        assert False, "expected 404 mapping"
    except HTTPException as exc:
        assert exc.status_code == 404
    assert len(calls) == 1


def test_events_cursor_skips_health_and_reuses_cursor(monkeypatch):
    settings = Settings(contract_id="C" + "A" * 55)
    captured = {}
    def fake_rpc(method, params, selected):
        captured.update(params)
        return {"events": [], "cursor": None}
    monkeypatch.setattr(stellar, "_rpc", fake_rpc)
    stellar.list_flag_events(10, cursor="opaque-cursor", settings=settings)
    assert captured["pagination"]["cursor"] == "opaque-cursor"
    assert "startLedger" not in captured
