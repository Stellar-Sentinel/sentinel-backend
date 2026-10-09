import base64
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from fastapi import HTTPException

from app import stellar
from app.config import Settings
from app.main import app
from app.stellar_address import is_valid_account_id

client = TestClient(app)


def make_strkey(version: int, payload: bytes) -> str:
    data = bytes([version]) + payload
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return base64.b32encode(data + crc.to_bytes(2, "little")).decode("ascii").rstrip("=")


ADDRESS = make_strkey(48, bytes([1]) * 32)
MUXED_ADDRESS = make_strkey(96, bytes([2]) * 40)


def test_health_and_cors():
    assert client.get("/health").json() == {"status": "ok"}
    response = client.options("/risk/score", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_classic_and_muxed_account_ids_are_checksum_validated(monkeypatch):
    assert is_valid_account_id(ADDRESS)
    assert is_valid_account_id(MUXED_ADDRESS)
    assert len(ADDRESS) == 56
    assert len(MUXED_ADDRESS) == 69

    def fake_get(url, params=None, settings=None):
        if url.endswith("/accounts/" + MUXED_ADDRESS):
            return {"sequence": "10", "balances": []}
        if url.endswith("/accounts/" + ADDRESS):
            return {"sequence": "10", "balances": []}
        return {"_embedded": {"records": []}}

    monkeypatch.setattr(stellar, "_get", fake_get)
    response = client.post("/risk/score", json={"address": MUXED_ADDRESS})
    assert response.status_code == 200
    assert response.json()["address"] == MUXED_ADDRESS

    corrupted = MUXED_ADDRESS[:-2] + ("A" if MUXED_ADDRESS[-2] != "A" else "B") + MUXED_ADDRESS[-1]
    assert not is_valid_account_id(corrupted)
    assert client.post("/risk/score", json={"address": corrupted}).status_code == 422


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
