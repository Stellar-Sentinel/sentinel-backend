from app import stellar
from app.config import Settings
from app.event_store import EventStore


def test_sync_stops_when_rpc_cursor_stops_advancing(monkeypatch, tmp_path):
    store = EventStore(str(tmp_path / "events.sqlite3"))
    settings = Settings(contract_id="C" + "A" * 55)
    calls = []

    def rpc(method, params, selected):
        if method == "getHealth":
            return {"latestLedger": 100, "oldestLedger": 1}
        calls.append(params.get("pagination", {}).get("cursor"))
        return {"events": [], "cursor": "stalled-cursor"}

    monkeypatch.setattr(stellar, "_rpc", rpc)
    stellar.sync_flag_events(store, settings)

    assert calls == [None, "stalled-cursor"]
    assert store.ingestion_cursor(f"{settings.network_passphrase}:{settings.contract_id}") == "stalled-cursor"
