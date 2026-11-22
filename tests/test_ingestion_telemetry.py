"""Telemetry, failure diagnostics, and the time budget for event ingestion.

RPC is replaced by a fake pooled HTTP client or a scripted `_rpc`, and every
test gets fresh process-local metrics, so nothing here touches a network or
depends on test order.
"""
import asyncio
import logging
import sqlite3

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main, stellar
from app.config import Settings
from app.event_store import EventStore
from app.metrics import INGESTION_FAILURES, INGESTION_STOP_REASONS, IngestionMetrics, ingestion_metrics
from app.routers import events as events_router

CONTRACT = "C" + "A" * 55
# Text an upstream could put in an error body; it must never reach logs.
UPSTREAM_MARKER = "upstream-private-detail-7f3a"


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


class RawHttpClient:
    """Pooled-client stand-in replaying exceptions, JSON-RPC bodies, or raw responses."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def post(self, url, json, timeout):
        outcome = self.outcomes.pop(0)
        request = httpx.Request("POST", url)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, tuple):
            status, body = outcome
            return httpx.Response(status, content=body, request=request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, **outcome}, request=request)


def _page(*events, cursor=None) -> dict:
    return {"result": {"events": list(events), "cursor": cursor}}


@pytest.fixture(autouse=True)
def fresh_metrics():
    ingestion_metrics.reset()
    yield
    ingestion_metrics.reset()


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "events.sqlite3"))


@pytest.fixture
def resumed(store):
    """A store with a persisted cursor, so syncs skip the getHealth call."""
    settings = _settings()
    store.save_page([], _toid(500), _scope(settings))
    return settings


# -- metrics registry ----------------------------------------------------------


def test_unknown_labels_collapse_to_other_so_counters_stay_bounded():
    metrics = IngestionMetrics()
    metrics.record_sync("caught_up", 1, 2, 3.0)
    metrics.record_sync(f"surprise-{UPSTREAM_MARKER}", 0, 0, 0)
    metrics.record_failure("rpc_timeout", 0, 0, 1)
    metrics.record_failure("/etc/passwd", 0, 0, 1)

    totals = metrics.snapshot()["totals"]

    assert set(totals["syncs"]) == set(INGESTION_STOP_REASONS) | {"other"}
    assert set(totals["failures"]) == set(INGESTION_FAILURES) | {"other"}
    assert totals["syncs"]["other"] == 1
    assert totals["failures"]["other"] == 1
    assert UPSTREAM_MARKER not in str(metrics.snapshot())


def test_failure_streak_resets_on_success_but_not_on_a_skipped_sync():
    metrics = IngestionMetrics()
    metrics.record_failure("rpc_unavailable", 0, 0, 5)
    metrics.record_failure("rpc_unavailable", 1, 3, 5)
    metrics.record_sync("sync_in_progress", 0, 0, 0)

    snapshot = metrics.snapshot()
    assert snapshot["consecutive_failures"] == 2
    assert snapshot["last_success_at"] is None
    assert snapshot["last_sync"]["result"] == "failed"
    # Pages committed before a failure are durable progress.
    assert (snapshot["totals"]["pages"], snapshot["totals"]["events"]) == (1, 3)

    metrics.record_sync("partial_page", 2, 4, 9.5, cursor_reset=True)
    snapshot = metrics.snapshot()
    assert snapshot["consecutive_failures"] == 0
    assert snapshot["last_success_at"] is not None
    assert snapshot["last_failure"]["failure"] == "rpc_unavailable"
    assert snapshot["totals"]["cursor_resets"] == 1
    assert snapshot["last_sync"] == {
        "result": "ok", "stop_reason": "partial_page", "failure": None,
        "pages": 2, "events": 4, "duration_ms": 9.5, "at": snapshot["last_success_at"],
    }


def test_snapshot_is_a_copy():
    metrics = IngestionMetrics()
    metrics.record_sync("caught_up", 1, 1, 1)
    metrics.snapshot()["totals"]["syncs"]["caught_up"] = 99
    metrics.snapshot()["last_sync"]["pages"] = 99
    assert metrics.snapshot()["totals"]["syncs"]["caught_up"] == 1
    assert metrics.snapshot()["last_sync"]["pages"] == 1


# -- failure classification through the real RPC path -------------------------


@pytest.mark.parametrize("outcome, failure, status", [
    (httpx.ReadTimeout("timed out"), "rpc_timeout", 503),
    (httpx.ConnectTimeout("timed out"), "rpc_timeout", 503),
    (httpx.ConnectError("refused"), "rpc_unavailable", 503),
    ((500, UPSTREAM_MARKER.encode()), "rpc_http_error", 502),
    ((200, b"<html>" + UPSTREAM_MARKER.encode()), "rpc_invalid_response", 502),
    ((200, b"[1, 2]"), "rpc_invalid_response", 502),
    ({"result": ["not", "an", "object"]}, "rpc_invalid_response", 502),
    ({"error": {"code": -32603, "message": UPSTREAM_MARKER}}, "rpc_error", 502),
    ({"result": {"events": {"not": "a list"}}}, "malformed_page", 502),
    ({"result": {"events": [{"id": "e1", "ledger": None}], "cursor": "next"}}, "malformed_page", 502),
    ({"result": {"events": [], "cursor": "bad cursor!"}}, "invalid_cursor", 502),
    ({"result": {"events": [], "cursor": _toid(1)}}, "cursor_regressed", 502),
])
def test_each_upstream_failure_has_one_fixed_label(monkeypatch, store, resumed, caplog,
                                                   outcome, failure, status):
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(outcome))

    with caplog.at_level(logging.WARNING, logger="sentinel.events"):
        with pytest.raises(HTTPException) as error:
            stellar.sync_flag_events(store, resumed)

    assert error.value.status_code == status
    assert stellar.classify_ingestion_failure(error.value) == failure
    assert ingestion_metrics.snapshot()["totals"]["failures"][failure] == 1
    record = next(r for r in caplog.records if getattr(r, "event", None) == "ingestion_sync_failed")
    assert record.failure == failure
    assert record.status_code == status
    assert UPSTREAM_MARKER not in caplog.text
    assert _toid(500) not in caplog.text
    assert store.ingestion_cursor(_scope(resumed)) == _toid(500)


@pytest.mark.parametrize("exc, failure", [
    (sqlite3.OperationalError("database is locked"), "store_error"),
    (HTTPException(status_code=503, detail="CONTRACT_ID is required"), "upstream_error"),
    (RuntimeError(UPSTREAM_MARKER), "internal_error"),
])
def test_non_rpc_failures_are_classified(exc, failure):
    assert stellar.classify_ingestion_failure(exc) == failure
    assert failure in INGESTION_FAILURES


def test_unexpected_error_logs_its_type_but_never_its_message(monkeypatch, store, resumed, caplog):
    def explode(*_args, **_kwargs):
        raise KeyError(UPSTREAM_MARKER)

    monkeypatch.setattr(stellar, "fetch_flag_event_page", explode)

    with caplog.at_level(logging.WARNING, logger="sentinel.events"):
        with pytest.raises(KeyError):
            stellar.sync_flag_events(store, resumed)

    record = next(r for r in caplog.records if getattr(r, "event", None) == "ingestion_sync_failed")
    assert (record.failure, record.error_type, record.status_code) == ("internal_error", "KeyError", None)
    assert UPSTREAM_MARKER not in caplog.text


def test_store_failure_is_classified_and_releases_the_scope_lock(monkeypatch, store, resumed):
    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "save_page", locked)
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(_page(cursor=_toid(501))))

    with pytest.raises(sqlite3.OperationalError):
        stellar.sync_flag_events(store, resumed)

    assert ingestion_metrics.snapshot()["last_failure"]["failure"] == "store_error"
    monkeypatch.delattr(store, "save_page")
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(_page()))
    assert stellar.sync_flag_events(store, resumed).stop_reason == "caught_up"


def test_failure_reports_pages_committed_before_it(monkeypatch, store, resumed, caplog):
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(
        _page(cursor=_toid(501)),
        _page(cursor=_toid(502)),
        httpx.ReadTimeout("timed out"),
    ))

    with caplog.at_level(logging.WARNING, logger="sentinel.events"):
        with pytest.raises(HTTPException):
            stellar.sync_flag_events(store, resumed)

    record = next(r for r in caplog.records if getattr(r, "event", None) == "ingestion_sync_failed")
    assert (record.failure, record.pages) == ("rpc_timeout", 2)
    assert store.ingestion_cursor(_scope(resumed)) == _toid(502)
    assert ingestion_metrics.snapshot()["totals"]["pages"] == 2


def test_halted_cursor_rejection_is_labelled_and_keeps_the_error_contract(monkeypatch, store, resumed):
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(
        {"error": {"code": -32600, "message": UPSTREAM_MARKER}}))

    with pytest.raises(stellar.SorobanRpcError) as error:
        stellar.sync_flag_events(store, resumed)

    assert error.value.code == -32600
    assert error.value.detail == "Soroban RPC returned an error"
    assert stellar.classify_ingestion_failure(error.value) == "cursor_rejected"
    assert ingestion_metrics.snapshot()["totals"]["failures"]["cursor_rejected"] == 1


def test_rejected_fresh_start_after_reset_is_an_rpc_error(monkeypatch, store, resumed):
    settings = _settings(event_ingest_cursor_recovery="reset")
    rejected = {"error": {"code": -32602, "message": UPSTREAM_MARKER}}
    health = {"result": {"latestLedger": 100_000, "oldestLedger": 90_000}}
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(rejected, health, rejected))

    with pytest.raises(stellar.SorobanRpcError) as error:
        stellar.sync_flag_events(store, settings)

    assert stellar.classify_ingestion_failure(error.value) == "rpc_error"
    snapshot = ingestion_metrics.snapshot()
    assert snapshot["last_failure"]["failure"] == "rpc_error"
    assert snapshot["totals"]["cursor_resets"] == 1


def test_successful_sync_and_skipped_sync_are_both_counted(monkeypatch, store, resumed, caplog):
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(
        _page(_rpc_event("e1"), cursor=_toid(501))))

    with caplog.at_level(logging.INFO, logger="sentinel.events"):
        result = stellar.sync_flag_events(store, resumed)
    lock = stellar._scope_lock(store, _scope(resumed))
    with lock:
        skipped = stellar.sync_flag_events(store, resumed)

    assert (result.stop_reason, skipped.stop_reason) == ("partial_page", "sync_in_progress")
    syncs = ingestion_metrics.snapshot()["totals"]["syncs"]
    assert (syncs["partial_page"], syncs["sync_in_progress"]) == (1, 1)
    record = next(r for r in caplog.records if getattr(r, "event", None) == "ingestion_sync")
    assert record.duration_ms >= 0
    assert "duration_ms=" in record.getMessage()


# -- malformed getHealth on a cold start ---------------------------------------


@pytest.mark.parametrize("health", [
    {"latestLedger": "lots", "oldestLedger": 1},
    {"latestLedger": True},
    {"latestLedger": -5},
    {"latestLedger": 100, "oldestLedger": [1]},
    {"latestLedger": 100, "oldestLedger": "1e9"},
])
def test_malformed_health_window_is_an_upstream_error_not_a_crash(monkeypatch, store, health):
    def rpc(method, _params, _settings):
        assert method == "getHealth", "getEvents must not run with an unknown start ledger"
        return health

    monkeypatch.setattr(stellar, "_rpc", rpc)

    with pytest.raises(stellar.UpstreamDataError) as error:
        stellar.sync_flag_events(store, _settings())

    assert error.value.status_code == 502
    assert ingestion_metrics.snapshot()["last_failure"]["failure"] == "rpc_invalid_response"


def test_health_window_accepts_numeric_strings_and_missing_fields(monkeypatch, store):
    starts = []

    def rpc(method, params, _settings):
        if method == "getHealth":
            return {"latestLedger": "100000"}
        starts.append(params["startLedger"])
        return {"events": []}

    monkeypatch.setattr(stellar, "_rpc", rpc)
    stellar.sync_flag_events(store, _settings(events_lookback_ledgers=1_000))

    assert starts == [99_000]


def test_events_endpoint_reports_stale_for_malformed_health(monkeypatch, store):
    settings = _settings()
    monkeypatch.setattr(stellar, "_rpc", lambda method, _params, _settings: {"latestLedger": "x"})
    monkeypatch.setattr(events_router, "get_settings", lambda: settings)
    monkeypatch.setattr(events_router, "get_event_store", lambda _path: store)

    response = TestClient(main.app).get("/events/")

    assert response.status_code == 200
    assert response.json()["source"]["ingestion_status"] == "stale"


# -- wall-clock time budget ----------------------------------------------------


class SteppingClock:
    """Monotonic clock that advances a fixed step every time it is read."""

    def __init__(self, step: float):
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_time_budget_stops_between_pages_and_next_sync_resumes(monkeypatch, store, resumed):
    settings = _settings(event_ingest_sync_budget_seconds=25)
    # Read once for the deadline (t=10 -> deadline 35), then once per extra page.
    monkeypatch.setattr(stellar, "_ingest_clock", SteppingClock(10))
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(
        *(_page(cursor=_toid(500 + n)) for n in range(1, 4))))

    result = stellar.sync_flag_events(store, settings)

    assert result.stop_reason == "time_budget_exhausted"
    assert result.pages == 3
    assert store.ingestion_cursor(_scope(settings)) == _toid(503)
    assert ingestion_metrics.snapshot()["totals"]["syncs"]["time_budget_exhausted"] == 1

    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(_page()))
    assert stellar.sync_flag_events(store, settings).stop_reason == "caught_up"


def test_an_exhausted_budget_still_attempts_one_page(monkeypatch, store, resumed):
    settings = _settings(event_ingest_sync_budget_seconds=0.001)
    monkeypatch.setattr(stellar, "_ingest_clock", SteppingClock(1_000))
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(
        _page(cursor=_toid(501)), _page(cursor=_toid(502))))

    result = stellar.sync_flag_events(store, settings)

    assert (result.stop_reason, result.pages) == ("time_budget_exhausted", 1)
    assert store.ingestion_cursor(_scope(settings)) == _toid(501)


@pytest.mark.parametrize("value", [0, -1, 3_601])
def test_sync_budget_setting_is_bounded(value):
    with pytest.raises(ValidationError):
        Settings(event_ingest_sync_budget_seconds=value)


def test_sync_budget_defaults_and_reads_from_environment(monkeypatch):
    assert Settings().event_ingest_sync_budget_seconds == 60
    monkeypatch.setenv("EVENT_INGEST_SYNC_BUDGET_SECONDS", "12.5")
    assert Settings().event_ingest_sync_budget_seconds == 12.5


# -- background loop -----------------------------------------------------------


def test_background_loop_logs_fixed_failure_label_and_backoff(monkeypatch, caplog):
    def fake_sync(_store, _settings):
        raise HTTPException(status_code=503, detail=UPSTREAM_MARKER) from httpx.ReadTimeout(UPSTREAM_MARKER)

    async def stop_after_first_sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(main, "settings", _settings(event_ingest_interval_seconds=10))
    monkeypatch.setattr(main, "get_event_store", lambda _path: object())
    monkeypatch.setattr(main, "sync_flag_events", fake_sync)
    monkeypatch.setattr(main.asyncio, "sleep", stop_after_first_sleep)

    with caplog.at_level(logging.WARNING, logger="app.main"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(main._ingest_events_forever())

    record = next(r for r in caplog.records if getattr(r, "event", None) == "ingestion_backoff")
    assert (record.failure, record.consecutive_failures, record.next_attempt_seconds) == ("rpc_timeout", 1, 20)
    assert UPSTREAM_MARKER not in caplog.text


# -- GET /events/ingestion -----------------------------------------------------


@pytest.fixture
def status_client(monkeypatch, store):
    def configure(settings: Settings):
        monkeypatch.setattr(events_router, "get_settings", lambda: settings)
        monkeypatch.setattr(events_router, "get_event_store", lambda _path: store)
        return TestClient(main.app)

    def no_sync(*_args, **_kwargs):
        raise AssertionError("ingestion status must not start a sync")

    monkeypatch.setattr(events_router, "sync_flag_events", no_sync)
    return configure


@pytest.fixture
def no_rpc(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("ingestion status must not call Soroban RPC")

    monkeypatch.setattr(stellar, "_rpc", refuse)


@pytest.mark.usefixtures("no_rpc")
def test_status_reports_disabled_without_a_contract(status_client):
    body = status_client(Settings(contract_id="")).get("/events/ingestion").json()

    assert (body["enabled"], body["state"], body["store"], body["cursor"]) == (False, "disabled", None, None)
    assert body["limits"]["cursor_recovery"] == "halt"


@pytest.mark.usefixtures("no_rpc")
def test_status_is_unknown_before_any_sync_in_this_process(status_client, store):
    settings = _settings()
    store.save_page([], _toid(123_456, 7), _scope(settings))

    response = status_client(settings).get("/events/ingestion")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "unknown"
    assert body["store"] == "ok"
    assert body["cursor"] == {"present": True, "ledger": 123_456}
    assert _toid(123_456, 7) not in response.text
    assert body["limits"] == {"page_size": 100, "max_pages": 25, "sync_budget_seconds": 60.0,
                              "interval_seconds": 30, "max_backoff_seconds": 300,
                              "cursor_recovery": "halt"}


def test_status_tracks_failure_then_recovery(status_client, monkeypatch, store, resumed):
    client = status_client(resumed)
    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(httpx.ConnectError("refused")))
    with pytest.raises(HTTPException):
        stellar.sync_flag_events(store, resumed)

    failing = client.get("/events/ingestion").json()
    assert failing["state"] == "failing"
    assert failing["consecutive_failures"] == 1
    assert failing["last_failure"]["failure"] == "rpc_unavailable"
    assert failing["last_sync"]["result"] == "failed"

    monkeypatch.setattr(stellar, "_http_client", RawHttpClient(_page(_rpc_event("e1"), cursor=_toid(900))))
    stellar.sync_flag_events(store, resumed)

    healthy = client.get("/events/ingestion").json()
    assert healthy["state"] == "ok"
    assert healthy["consecutive_failures"] == 0
    assert healthy["cursor"] == {"present": True, "ledger": 900}
    assert healthy["last_sync"]["stop_reason"] == "partial_page"
    assert healthy["totals"]["events"] == 1
    assert healthy["last_failure"]["failure"] == "rpc_unavailable"


def test_status_hides_opaque_cursor_and_reports_no_ledger(status_client, store):
    settings = _settings()
    store.save_page([], "opaque-provider-cursor", _scope(settings))

    response = status_client(settings).get("/events/ingestion")

    assert response.json()["cursor"] == {"present": True, "ledger": None}
    assert "opaque-provider-cursor" not in response.text


def test_status_degrades_when_the_store_is_unreadable(status_client, monkeypatch, store, caplog):
    def broken(_scope):
        raise sqlite3.DatabaseError(f"file is not a database {UPSTREAM_MARKER}")

    monkeypatch.setattr(store, "ingestion_cursor", broken)

    with caplog.at_level(logging.WARNING, logger="sentinel.events"):
        response = status_client(_settings()).get("/events/ingestion")

    assert response.status_code == 200
    assert (response.json()["store"], response.json()["cursor"]) == ("unavailable", None)
    assert UPSTREAM_MARKER not in caplog.text


def test_status_schema_is_published_with_an_example():
    schemas = TestClient(main.app).get("/openapi.json").json()["components"]["schemas"]
    assert schemas["IngestionStatusResponse"]["examples"]


@pytest.mark.parametrize("cursor, ledger", [
    (_toid(1), 1),
    # TOIDs are signed int64, so the largest encodable ledger is 2**31 - 1.
    (_toid(2**31 - 1, 9), 2**31 - 1),
    ("opaque", None),
    (None, None),
    ("", None),
])
def test_cursor_ledger_decodes_only_standard_cursors(cursor, ledger):
    assert stellar.cursor_ledger(cursor) == ledger
