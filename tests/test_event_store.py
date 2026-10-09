import pytest
from fastapi import HTTPException

from app import stellar
from app.event_store import EventStore
from app.config import Settings
from app.main import app
from app.routers import events as events_router
from fastapi.testclient import TestClient


SCOPE = "Test SDF Network ; September 2015:C" + "A" * 55


def _event(event_id, ledger, score):
    return {
        "id": event_id,
        "ledger": ledger,
        "created_at": f"ledger-{ledger}",
        "agent": "agent",
        "subject": "subject",
        "score": score,
        "contract_id": "C" + "A" * 55,
        "tx_hash": f"tx-{event_id}",
    }


def test_store_persists_cursor_and_deduplicates_events_after_restart(tmp_path):
    path = str(tmp_path / "events.sqlite3")
    store = EventStore(path)
    store.save_page([_event("event-1", 10, 70), _event("event-2", 11, 90)], "rpc-cursor-1", SCOPE)
    restarted = EventStore(path)

    assert restarted.ingestion_cursor(SCOPE) == "rpc-cursor-1"
    page = restarted.page(limit=1, scope=SCOPE)
    assert page["events"] == [_event("event-2", 11, 90)]
    assert page["next_cursor"]
    second_page = restarted.page(limit=1, scope=SCOPE, cursor=page["next_cursor"])
    assert second_page["events"] == [_event("event-1", 10, 70)]
    assert second_page["next_cursor"] is None

    restarted.save_page([_event("event-2", 11, 1)], "rpc-cursor-2", SCOPE)
    deduplicated = EventStore(path)
    assert deduplicated.ingestion_cursor(SCOPE) == "rpc-cursor-2"
    assert deduplicated.page(limit=10, scope=SCOPE)["events"][0]["score"] == 90


def test_store_separates_network_contract_scopes_and_rejects_bad_page_cursor(tmp_path):
    store = EventStore(str(tmp_path / "events.sqlite3"))
    other_scope = "Public Global Stellar Network ; September 2015:C" + "A" * 55
    store.save_page([_event("event-1", 10, 70)], "test-cursor", SCOPE)

    assert store.page(limit=10, scope=other_scope)["events"] == []
    with pytest.raises(HTTPException) as error:
        store.page(limit=10, scope=SCOPE, cursor="invalid")
    assert error.value.status_code == 422


def test_malformed_event_page_does_not_advance_ingestion_cursor(tmp_path):
    store = EventStore(str(tmp_path / "events.sqlite3"))
    with pytest.raises(ValueError, match="missing its ID or ledger"):
        store.save_page([{"id": "missing-ledger"}], "must-not-advance", SCOPE)
    assert store.ingestion_cursor(SCOPE) is None
    assert store.page(limit=10, scope=SCOPE)["events"] == []


def test_ingestion_resumes_from_persisted_rpc_cursor_and_keeps_cursor_on_rpc_error(monkeypatch, tmp_path):
    store = EventStore(str(tmp_path / "events.sqlite3"))
    settings = Settings(contract_id="C" + "A" * 55)
    scope = f"{settings.network_passphrase}:{settings.contract_id}"
    store.save_page([_event("event-1", 10, 70)], "resume-here", scope)
    calls = []

    def rpc(method, params, selected):
        calls.append((method, params))
        return {
            "events": [{
                "id": "event-2",
                "ledger": 12,
                "ledgerClosedAt": "ledger-12",
                "contractId": selected.contract_id,
                "topic": [{"symbol": "flagged"}, {"address": {"accountId": "agent"}},
                          {"address": {"accountId": "subject"}}],
                "value": {"u32": 80},
                "txHash": "tx-2",
            }],
            "cursor": "resume-next",
        }

    monkeypatch.setattr(stellar, "_rpc", rpc)
    stellar.sync_flag_events(store, settings)

    assert calls[0][0] == "getEvents"
    assert calls[0][1]["pagination"]["cursor"] == "resume-here"
    assert store.ingestion_cursor(scope) == "resume-next"
    assert store.page(limit=10, scope=scope)["events"][0]["id"] == "event-2"

    def failing_rpc(*_args):
        raise HTTPException(status_code=502, detail="Soroban RPC request failed")

    monkeypatch.setattr(stellar, "_rpc", failing_rpc)
    with pytest.raises(HTTPException) as error:
        stellar.sync_flag_events(EventStore(str(tmp_path / "events.sqlite3")), settings)
    assert error.value.status_code == 502
    assert EventStore(str(tmp_path / "events.sqlite3")).ingestion_cursor(scope) == "resume-next"


def test_events_endpoint_pages_persisted_records_with_compatible_shape(monkeypatch, tmp_path):
    settings = Settings(contract_id="C" + "A" * 55)
    scope = f"{settings.network_passphrase}:{settings.contract_id}"
    store = EventStore(str(tmp_path / "events.sqlite3"))
    store.save_page([_event("event-1", 10, 70), _event("event-2", 11, 90)], "rpc-cursor", scope)
    monkeypatch.setattr(events_router, "get_settings", lambda: settings)
    monkeypatch.setattr(events_router, "get_event_store", lambda _path: store)
    monkeypatch.setattr(events_router, "sync_flag_events", lambda *_args: None)
    client = TestClient(app)

    first = client.get("/events/?limit=1")
    assert first.status_code == 200
    payload = first.json()
    assert payload["events"][0]["id"] == "event-2"
    assert payload["source"]["contract_id"] == settings.contract_id
    second = client.get(f"/events/?limit=1&cursor={payload['next_cursor']}")
    assert second.json()["events"][0]["id"] == "event-1"


def test_events_endpoint_serves_indexed_history_when_rpc_is_unavailable(monkeypatch, tmp_path):
    settings = Settings(contract_id="C" + "A" * 55)
    scope = f"{settings.network_passphrase}:{settings.contract_id}"
    store = EventStore(str(tmp_path / "events.sqlite3"))
    store.save_page([_event("event-1", 10, 70)], "rpc-cursor", scope)

    def fail_sync(*_args):
        raise HTTPException(status_code=503, detail="Unable to reach configured Soroban RPC")

    monkeypatch.setattr(events_router, "get_settings", lambda: settings)
    monkeypatch.setattr(events_router, "get_event_store", lambda _path: store)
    monkeypatch.setattr(events_router, "sync_flag_events", fail_sync)
    response = TestClient(app).get("/events/")

    assert response.status_code == 200
    assert response.json()["events"][0]["id"] == "event-1"
    assert response.json()["source"]["ingestion_status"] == "stale"
