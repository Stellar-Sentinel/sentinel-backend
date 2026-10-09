import json
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
    assert client.get("/live").json() == {"status": "ok"}
    response = client.options("/risk/score", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_request_log_uses_route_template_without_sensitive_inputs(caplog):
    address = "G" + "Z" * 55
    with caplog.at_level("INFO", logger="sentinel.request"):
        response = client.get(f"/missing/{address}?token=do-not-log")

    assert response.status_code == 404
    log_record = json.loads(caplog.records[-1].message)
    assert log_record["method"] == "GET"
    assert log_record["route"] == "<unmatched>"
    assert log_record["status"] == 404
    assert address not in caplog.text
    assert "do-not-log" not in caplog.text
    assert "token" not in caplog.text


def test_readiness_reports_each_dependency(monkeypatch):
    monkeypatch.setattr(stellar, "_get", lambda *args, **kwargs: {})
    monkeypatch.setattr(stellar, "_rpc", lambda *args, **kwargs: {"status": "healthy"})

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"horizon": "ok", "soroban_rpc": "ok"},
    }


def test_readiness_returns_503_without_leaking_upstream_errors(monkeypatch):
    def fail_horizon(*args, **kwargs):
        raise HTTPException(status_code=503, detail="private upstream endpoint")

    monkeypatch.setattr(stellar, "_get", fail_horizon)
    monkeypatch.setattr(stellar, "_rpc", lambda *args, **kwargs: {"status": "healthy"})

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"horizon": "unavailable", "soroban_rpc": "ok"},
    }
    assert "private upstream endpoint" not in response.text


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


def test_risk_thresholds_and_weights_are_configurable_and_bounded(monkeypatch):
    recent = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(stellar, "_get", lambda url, params=None, settings=None: (
        {"sequence": "50", "balances": []}
        if url.endswith("/accounts/" + ADDRESS)
        else {"_embedded": {"records": [{
            "created_at": recent,
            "type": "payment",
            "source_account": ADDRESS,
            "to": "G" + "B" * 55,
            "asset_type": "native",
            "amount": "120",
        }]}}
    ))
    settings = Settings(
        risk_activity_burst_min_operations=1,
        risk_activity_burst_points=50,
        risk_transfer_volume_xlm_threshold=100,
        risk_transfer_volume_points=80,
        risk_high_score_threshold=60,
        risk_elevated_score_threshold=30,
    )

    result = stellar.score_account(ADDRESS, settings=settings)

    assert result["score"] == 100
    assert result["risk_level"] == "high"
    assert result["threshold"] == 60
    assert result["threshold_exceeded"] is True
    assert "at least 1 operations" in result["signals"][0]["explanation"]


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


def test_upstream_requests_use_one_managed_client():
    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = False

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            return httpx.Response(200, json={"fee_stats": True})

        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            return httpx.Response(200, json={"result": {"status": "healthy"}})

        def close(self):
            self.closed = True

    settings = Settings(request_timeout_seconds=2.5)
    fake_client = FakeClient()
    stellar.set_http_client(fake_client)
    try:
        stellar._get("https://horizon.example/fee_stats", settings=settings)
        stellar._rpc("getHealth", {}, settings=settings)
    finally:
        stellar.close_http_client()

    assert [call[0] for call in fake_client.calls] == ["GET", "POST"]
    assert all(call[2]["timeout"] == 2.5 for call in fake_client.calls)
    assert fake_client.closed is True


def test_application_lifespan_closes_shared_client():
    with TestClient(app):
        shared_client = stellar._http_client
        assert shared_client is not None
        assert not shared_client.is_closed

    assert shared_client.is_closed
    assert stellar._http_client is None


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
