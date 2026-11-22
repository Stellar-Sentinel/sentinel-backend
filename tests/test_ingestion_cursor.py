"""Cursor management for Soroban event ingestion.

Every test runs against a temporary SQLite store with RPC replaced by a fake
(either `_rpc` itself or the pooled HTTP client below it), so nothing here
touches a live network.
"""
import asyncio
import logging
import threading

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main, stellar
from app.config import Settings
from app.event_store import CursorConflictError, EventStore
from app.routers import events as events_router

CONTRACT = "C" + "A" * 55


def _settings(**overrides) -> Settings:
    return Settings(contract_id=CONTRACT, upstream_max_retries=0, **overrides)


def _scope(settings: Settings) -> str:
    return f"{settings.network_passphrase}:{settings.contract_id}"


def _rpc_event(event_id: str, ledger: int = 10) -> dict:
    return {
        "id": event_id, "ledger": ledger, "ledgerClosedAt": f"ledger-{ledger}",
        "contractId": CONTRACT,
        "topicJson": [{"symbol": "flagged"}, {"address": "agent"}, {"address": "subject"}],
        "valueJson": {"u32": 80}, "txHash": f"tx-{event_id}",
    }


def _toid(ledger: int, index: int = 0) -> str:
    return f"{ledger << 32:019d}-{index:010d}"


class ScriptedRpc:
    """Fake `_rpc` that answers getEvents from a fixed list of responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, method, params, _settings):
        if method == "getHealth":
            return {"latestLedger": 100_000, "oldestLedger": 90_000}
        self.requests.append(params)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    @property
    def cursors(self):
        return [request.get("pagination", {}).get("cursor") for request in self.requests]


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "events.sqlite3"))


# -- forward progress and catch-up bounds ---------------------------------


def test_full_pages_keep_paging_until_a_partial_page(monkeypatch, store):
    settings = _settings(event_ingest_page_size=2)
    rpc = ScriptedRpc(
        {"events": [_rpc_event("e1"), _rpc_event("e2")], "cursor": "c1"},
        {"events": [_rpc_event("e3"), _rpc_event("e4")], "cursor": "c2"},
        {"events": [_rpc_event("e5")], "cursor": "c3"},
    )
    monkeypatch.setattr(stellar, "_rpc", rpc)

    result = stellar.sync_flag_events(store, settings)

    assert result.stop_reason == "partial_page"
    assert (result.pages, result.events, result.cursor) == (3, 5, "c3")
    assert [request["pagination"]["limit"] for request in rpc.requests] == [2, 2, 2]
    assert rpc.cursors == [None, "c1", "c2"]
    assert store.ingestion_cursor(_scope(settings)) == "c3"
    assert len(store.page(limit=10, scope=_scope(settings))["events"]) == 5


def test_configured_page_budget_bounds_one_sync_and_next_sync_resumes(monkeypatch, store):
    settings = _settings(event_ingest_max_pages=3)
    rpc = ScriptedRpc(*({"events": [], "cursor": f"empty-{n}"} for n in range(1, 7)))
    monkeypatch.setattr(stellar, "_rpc", rpc)

    first = stellar.sync_flag_events(store, settings)
    assert first.stop_reason == "page_budget_exhausted"
    assert first.pages == 3
    assert store.ingestion_cursor(_scope(settings)) == "empty-3"

    second = stellar.sync_flag_events(store, settings)
    assert rpc.cursors[3] == "empty-3"
    assert second.stop_reason == "page_budget_exhausted"
    assert store.ingestion_cursor(_scope(settings)) == "empty-6"
    assert rpc.responses == []


def test_missing_continuation_cursor_reports_caught_up_and_keeps_cursor(monkeypatch, store):
    settings = _settings()
    store.save_page([], "resume", _scope(settings))
    monkeypatch.setattr(stellar, "_rpc", ScriptedRpc({"events": []}))

    result = stellar.sync_flag_events(store, settings)

    assert result.stop_reason == "caught_up"
    assert result.cursor == "resume"
    assert store.ingestion_cursor(_scope(settings)) == "resume"


def test_scope_is_per_network_and_contract(monkeypatch, store):
    testnet = _settings()
    other_contract = Settings(contract_id="C" + "B" * 55, upstream_max_retries=0)
    store.save_page([], "testnet-cursor", stellar.ingestion_scope(testnet))
    rpc = ScriptedRpc({"events": [], "cursor": None})
    monkeypatch.setattr(stellar, "_rpc", rpc)

    stellar.sync_flag_events(store, other_contract)

    # A new contract starts from the lookback window, not another scope's cursor.
    assert rpc.cursors == [None]
    assert "startLedger" in rpc.requests[0]
    assert store.ingestion_cursor(stellar.ingestion_scope(testnet)) == "testnet-cursor"


# -- malformed upstream data ------------------------------------------------


def test_cursor_that_moves_backwards_is_rejected_without_writing(monkeypatch, store):
    settings = _settings()
    store.save_page([], _toid(500), _scope(settings))
    monkeypatch.setattr(stellar, "_rpc", ScriptedRpc(
        {"events": [_rpc_event("late", 400)], "cursor": _toid(400)}))

    with pytest.raises(HTTPException) as error:
        stellar.sync_flag_events(store, settings)

    assert error.value.status_code == 502
    assert store.ingestion_cursor(_scope(settings)) == _toid(500)
    assert store.page(limit=10, scope=_scope(settings))["events"] == []


def test_cursor_cycle_is_rejected_instead_of_rewinding(monkeypatch, store):
    settings = _settings()
    monkeypatch.setattr(stellar, "_rpc", ScriptedRpc(
        {"events": [], "cursor": "a"},
        {"events": [], "cursor": "b"},
        {"events": [], "cursor": "a"},
    ))

    with pytest.raises(HTTPException) as error:
        stellar.sync_flag_events(store, settings)

    assert error.value.detail == "Soroban RPC event cursor moved backwards"
    assert store.ingestion_cursor(_scope(settings)) == "b"


@pytest.mark.parametrize("bad_cursor", [12345, "x" * 129, "cursor with spaces", "cursor\n", ["c1"]])
def test_malformed_cursor_is_never_persisted(monkeypatch, store, bad_cursor):
    settings = _settings()
    monkeypatch.setattr(stellar, "_rpc", ScriptedRpc({"events": [_rpc_event("e1")], "cursor": bad_cursor}))

    with pytest.raises(HTTPException) as error:
        stellar.sync_flag_events(store, settings)

    assert error.value.detail == "Soroban RPC returned an invalid event cursor"
    assert store.ingestion_cursor(_scope(settings)) is None
    assert store.page(limit=10, scope=_scope(settings))["events"] == []


@pytest.mark.parametrize("bad_events", [{"id": "e1"}, "events", [_rpc_event("ok"), "not-an-event"]])
def test_malformed_event_list_is_a_bad_gateway(monkeypatch, store, bad_events):
    settings = _settings()
    monkeypatch.setattr(stellar, "_rpc", ScriptedRpc({"events": bad_events, "cursor": "c1"}))

    with pytest.raises(HTTPException) as error:
        stellar.sync_flag_events(store, settings)

    assert error.value.status_code == 502
    assert store.ingestion_cursor(_scope(settings)) is None


def test_event_without_ledger_rejects_page_as_bad_gateway(monkeypatch, store):
    settings = _settings()
    store.save_page([], "before", _scope(settings))
    broken = _rpc_event("e1")
    del broken["ledger"]
    monkeypatch.setattr(stellar, "_rpc", ScriptedRpc({"events": [broken], "cursor": "after"}))

    with pytest.raises(HTTPException) as error:
        stellar.sync_flag_events(store, settings)

    assert error.value.detail == "Soroban RPC returned a malformed event"
    assert store.ingestion_cursor(_scope(settings)) == "before"


def test_events_endpoint_serves_stale_history_for_malformed_upstream_page(monkeypatch, store):
    settings = _settings()
    store.save_page([{"id": "kept", "ledger": 5, "score": 70}], "before", _scope(settings))
    broken = _rpc_event("e1")
    broken["ledger"] = "not-a-ledger"
    monkeypatch.setattr(stellar, "_rpc", ScriptedRpc({"events": [broken], "cursor": "after"}))
    monkeypatch.setattr(events_router, "get_settings", lambda: settings)
    monkeypatch.setattr(events_router, "get_event_store", lambda _path: store)

    response = TestClient(main.app).get("/events/")

    assert response.status_code == 200
    assert response.json()["source"]["ingestion_status"] == "stale"
    assert [event["id"] for event in response.json()["events"]] == ["kept"]


def test_events_endpoint_does_not_wait_for_an_in_flight_sync(monkeypatch, store):
    settings = _settings()
    store.save_page([{"id": "kept", "ledger": 5, "score": 70}], "c1", _scope(settings))
    monkeypatch.setattr(events_router, "get_settings", lambda: settings)
    monkeypatch.setattr(events_router, "get_event_store", lambda _path: store)
    monkeypatch.setattr(events_router, "sync_flag_events",
                        lambda *_args: stellar.IngestionSyncResult(_scope(settings), "sync_in_progress"))

    response = TestClient(main.app).get("/events/")

    assert response.status_code == 200
    assert response.json()["source"]["ingestion_status"] == "current"


# -- timeouts and unavailable RPC ------------------------------------------


class FakeHttpClient:
    """Stands in for the pooled httpx.Client; replays scripted outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.payloads = []

    def post(self, url, json, timeout):
        self.payloads.append(json)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, **outcome},
                              request=httpx.Request("POST", url))


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("timed out"), httpx.ConnectError("refused")])
def test_rpc_timeout_or_outage_keeps_progress_from_earlier_pages(monkeypatch, store, failure):
    settings = _settings()
    store.save_page([], "start", _scope(settings))
    client = FakeHttpClient(
        {"result": {"events": [], "cursor": "page-1"}},
        failure,
    )
    monkeypatch.setattr(stellar, "_http_client", client)

    with pytest.raises(HTTPException) as error:
        stellar.sync_flag_events(store, settings)

    assert error.value.status_code == 503
    assert store.ingestion_cursor(_scope(settings)) == "page-1"
    # Recovery is simply the next sync: it resumes from the committed cursor.
    monkeypatch.setattr(stellar, "_http_client", FakeHttpClient(
        {"result": {"events": [_rpc_event("e1")], "cursor": "page-2"}}))
    assert stellar.sync_flag_events(store, settings).cursor == "page-2"


def test_json_rpc_error_keeps_code_but_not_provider_message(monkeypatch):
    client = FakeHttpClient({"error": {"code": -32600, "message": "secret-ish provider text"}})
    monkeypatch.setattr(stellar, "_http_client", client)

    with pytest.raises(stellar.SorobanRpcError) as error:
        stellar._rpc("getEvents", {}, _settings())

    assert isinstance(error.value, HTTPException)
    assert error.value.status_code == 502
    assert error.value.code == -32600
    assert "provider text" not in error.value.detail


@pytest.mark.parametrize("error_body", ["boom", {"code": "bad"}, {"code": True}, {"message": "no code"}])
def test_json_rpc_error_without_usable_code(monkeypatch, error_body):
    monkeypatch.setattr(stellar, "_http_client", FakeHttpClient({"error": error_body}))

    with pytest.raises(stellar.SorobanRpcError) as error:
        stellar._rpc("getEvents", {}, _settings())

    assert error.value.code is None


# -- recovery when the provider rejects the persisted cursor ---------------


def test_halt_policy_keeps_rejected_cursor_and_logs_only_the_code(monkeypatch, store, caplog):
    settings = _settings()
    store.save_page([], "expired", _scope(settings))
    monkeypatch.setattr(stellar, "_rpc", ScriptedRpc(stellar.SorobanRpcError(-32600)))

    with caplog.at_level(logging.WARNING, logger="sentinel.events"):
        with pytest.raises(stellar.SorobanRpcError):
            stellar.sync_flag_events(store, settings)

    assert store.ingestion_cursor(_scope(settings)) == "expired"
    assert "rpc_error_code=-32600 recovery=halt" in caplog.text
    assert "expired" not in caplog.text


def test_reset_policy_restarts_from_lookback_window_and_keeps_history(monkeypatch, store):
    settings = _settings(event_ingest_cursor_recovery="reset")
    store.save_page([{"id": "old", "ledger": 5, "score": 70}], "expired", _scope(settings))
    rpc = ScriptedRpc(
        stellar.SorobanRpcError(-32600),
        {"events": [_rpc_event("fresh", 90_001)], "cursor": "fresh-cursor"},
    )
    monkeypatch.setattr(stellar, "_rpc", rpc)

    result = stellar.sync_flag_events(store, settings)

    assert result.cursor_reset is True
    assert result.stop_reason == "partial_page"
    assert rpc.cursors == ["expired", None]
    assert rpc.requests[1]["startLedger"] == 90_000
    assert store.ingestion_cursor(_scope(settings)) == "fresh-cursor"
    assert [e["id"] for e in store.page(limit=10, scope=_scope(settings))["events"]] == ["fresh", "old"]


def test_reset_policy_resets_once_then_surfaces_the_failure(monkeypatch, store):
    settings = _settings(event_ingest_cursor_recovery="reset")
    store.save_page([], "expired", _scope(settings))
    rpc = ScriptedRpc(stellar.SorobanRpcError(-32602), stellar.SorobanRpcError(-32602))
    monkeypatch.setattr(stellar, "_rpc", rpc)

    with pytest.raises(stellar.SorobanRpcError):
        stellar.sync_flag_events(store, settings)

    assert rpc.cursors == ["expired", None]
    assert store.ingestion_cursor(_scope(settings)) is None


@pytest.mark.parametrize("code", [-32603, None])
def test_reset_policy_ignores_errors_that_are_not_cursor_rejections(monkeypatch, store, code):
    settings = _settings(event_ingest_cursor_recovery="reset")
    store.save_page([], "still-valid", _scope(settings))
    monkeypatch.setattr(stellar, "_rpc", ScriptedRpc(stellar.SorobanRpcError(code)))

    with pytest.raises(stellar.SorobanRpcError):
        stellar.sync_flag_events(store, settings)

    assert store.ingestion_cursor(_scope(settings)) == "still-valid"


# -- concurrent syncs --------------------------------------------------------


def test_second_sync_in_the_same_process_returns_without_fetching(monkeypatch, store):
    settings = _settings()
    overlapping = []

    def rpc(method, params, selected):
        if method == "getHealth":
            return {"latestLedger": 100, "oldestLedger": 1}
        worker = threading.Thread(target=lambda: overlapping.append(stellar.sync_flag_events(store, selected)))
        worker.start()
        worker.join()
        return {"events": [], "cursor": None}

    monkeypatch.setattr(stellar, "_rpc", rpc)
    result = stellar.sync_flag_events(store, settings)

    assert result.stop_reason == "caught_up"
    assert [r.stop_reason for r in overlapping] == ["sync_in_progress"]
    assert overlapping[0].pages == 0


def test_other_process_advancing_cursor_wins_and_is_not_rewound(monkeypatch, store):
    settings = _settings()
    scope = _scope(settings)
    store.save_page([], "shared", scope)
    other_worker = EventStore(store.path)

    def rpc(method, params, _selected):
        # While this sync waits on RPC, another process commits further progress.
        other_worker.save_page([], "advanced-elsewhere", scope, expected_cursor="shared")
        return {"events": [_rpc_event("e1")], "cursor": "stale-page-cursor"}

    monkeypatch.setattr(stellar, "_rpc", rpc)
    result = stellar.sync_flag_events(store, settings)

    assert result.stop_reason == "cursor_conflict"
    assert result.pages == 0
    assert store.ingestion_cursor(scope) == "advanced-elsewhere"
    assert store.page(limit=10, scope=scope)["events"] == []


def test_store_compare_and_set_and_guarded_reset(store):
    scope = "net:contract"
    store.save_page([], "c1", scope, expected_cursor=None)

    with pytest.raises(CursorConflictError):
        store.save_page([{"id": "e1", "ledger": 1, "score": 1}], "c2", scope, expected_cursor=None)
    assert store.ingestion_cursor(scope) == "c1"
    assert store.page(limit=10, scope=scope)["events"] == []

    assert store.reset_ingestion_cursor(scope, "not-c1") is False
    assert store.ingestion_cursor(scope) == "c1"
    assert store.reset_ingestion_cursor(scope, "c1") is True
    assert store.ingestion_cursor(scope) is None


# -- configuration and background backoff ----------------------------------


@pytest.mark.parametrize("field, value", [
    ("event_ingest_page_size", 0),
    ("event_ingest_page_size", 1_001),
    ("event_ingest_max_pages", 0),
    ("event_ingest_max_pages", 101),
    ("event_ingest_max_backoff_seconds", 0),
    ("event_ingest_cursor_recovery", "rewind"),
])
def test_ingestion_settings_reject_out_of_range_values(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_ingestion_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("EVENT_INGEST_PAGE_SIZE", "250")
    monkeypatch.setenv("EVENT_INGEST_MAX_PAGES", "10")
    monkeypatch.setenv("EVENT_INGEST_MAX_BACKOFF_SECONDS", "600")
    monkeypatch.setenv("EVENT_INGEST_CURSOR_RECOVERY", "reset")

    settings = Settings()

    assert (settings.event_ingest_page_size, settings.event_ingest_max_pages) == (250, 10)
    assert settings.event_ingest_max_backoff_seconds == 600
    assert settings.event_ingest_cursor_recovery == "reset"


def test_defaults_preserve_previous_catchup_bounds():
    settings = Settings()
    assert settings.event_ingest_page_size == 100
    assert settings.event_ingest_max_pages == stellar._MAX_CATCHUP_PAGES == 25
    assert settings.event_ingest_cursor_recovery == "halt"


@pytest.mark.parametrize("failures, expected", [(0, 30), (1, 60), (2, 120), (3, 240), (4, 300), (500, 300)])
def test_retry_delay_doubles_per_failure_up_to_the_cap(failures, expected):
    assert stellar.ingestion_retry_delay(failures, Settings()) == expected


def test_retry_delay_never_drops_below_the_poll_interval():
    settings = Settings(event_ingest_interval_seconds=900, event_ingest_max_backoff_seconds=300)
    assert stellar.ingestion_retry_delay(3, settings) == 900


def test_background_loop_backs_off_on_failures_and_resets_after_success(monkeypatch):
    outcomes = [HTTPException(503), HTTPException(502), None, HTTPException(503)]
    delays = []

    def fake_sync(_store, _settings):
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    async def fake_sleep(seconds):
        delays.append(seconds)
        if not outcomes:
            raise asyncio.CancelledError

    monkeypatch.setattr(main, "settings", _settings(event_ingest_interval_seconds=10,
                                                     event_ingest_max_backoff_seconds=35))
    monkeypatch.setattr(main, "get_event_store", lambda _path: object())
    monkeypatch.setattr(main, "sync_flag_events", fake_sync)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._ingest_events_forever())

    assert delays == [20, 35, 10, 20]
