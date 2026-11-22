"""Versioned, additive event-store migrations and their failure behavior.

Every test works on a temporary SQLite file; none touches Horizon, Soroban
RPC, signing keys, or production data.
"""
import asyncio
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import event_store, main, stellar
from app.config import Settings
from app.event_store import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    AddColumn,
    CreateTable,
    EventStore,
    EventStoreSchemaError,
    Migration,
    _validate_catalog,
)
from app.routers import events as events_router

SCOPE = "Test SDF Network ; September 2015:C" + "A" * 55

# The flag_events shape shipped before report_digest existed.
PRE_DIGEST_FLAG_EVENTS = """CREATE TABLE flag_events (
    scope TEXT NOT NULL,
    event_id TEXT NOT NULL,
    ledger INTEGER NOT NULL,
    created_at TEXT,
    agent TEXT,
    subject TEXT,
    score_json TEXT,
    contract_id TEXT,
    tx_hash TEXT,
    PRIMARY KEY (scope, event_id)
)"""


def _event(event_id, ledger, report_digest=None):
    return {
        "id": event_id, "ledger": ledger, "created_at": f"ledger-{ledger}",
        "agent": "agent", "subject": "subject", "score": 80,
        "contract_id": "C" + "A" * 55, "tx_hash": f"tx-{event_id}", "report_digest": report_digest,
    }


def _history(path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()


def _schema(path):
    """Column and index definitions that every fully migrated file must share."""
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f"PRAGMA table_info({table})").fetchall()
            for table in ("flag_events", "ingestion_state", "schema_migrations")
        } | {
            "indexes": connection.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL ORDER BY name"
            ).fetchall()
        }


def _table_names(path):
    with sqlite3.connect(path) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _create_pre_digest_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute(PRE_DIGEST_FLAG_EVENTS)
        connection.execute(
            "CREATE INDEX idx_flag_events_page ON flag_events(scope, ledger DESC, event_id DESC)"
        )
        connection.execute(
            "INSERT INTO flag_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (SCOPE, "legacy-event", 5, "ledger-5", "agent", "subject", "70", "C" + "A" * 55, "tx-legacy"),
        )
        connection.execute("CREATE TABLE ingestion_state (scope TEXT PRIMARY KEY, cursor TEXT)")
        connection.execute("INSERT INTO ingestion_state VALUES (?, ?)", (SCOPE, "legacy-cursor"))


def _create_newer_database(path):
    """A file migrated by a hypothetical later release that added a nullable column."""
    EventStore(str(path)).save_page([_event("event-1", 10)], "cursor-1", SCOPE)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE flag_events ADD COLUMN reviewer_note TEXT")
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (LATEST_SCHEMA_VERSION + 1, "add_reviewer_note", "2027-01-01T00:00:00+00:00"),
        )


def _schema_records(caplog):
    return [record for record in caplog.records if record.name == "sentinel.event_store"]


# -- Upgrades ------------------------------------------------------------------


def test_fresh_database_records_every_migration_in_order(tmp_path, caplog):
    path = tmp_path / "events.sqlite3"

    with caplog.at_level(logging.INFO, logger="sentinel.event_store"):
        EventStore(str(path))

    assert [(version, name) for version, name, _ in _history(path)] == [
        (migration.version, migration.name) for migration in MIGRATIONS
    ]
    statuses = [(record.migration, record.status, record.schema_version) for record in _schema_records(caplog)]
    assert statuses == [("create_event_tables", "applied", 1), ("add_report_digest", "applied", 2)]
    assert str(path) not in caplog.text


def test_legacy_database_upgrades_to_the_same_schema_as_a_fresh_one(tmp_path, caplog):
    legacy = tmp_path / "legacy.sqlite3"
    fresh = tmp_path / "fresh.sqlite3"
    _create_pre_digest_database(legacy)

    with caplog.at_level(logging.INFO, logger="sentinel.event_store"):
        store = EventStore(str(legacy))
    EventStore(str(fresh))

    assert _schema(legacy) == _schema(fresh)
    statuses = [(record.migration, record.status) for record in _schema_records(caplog)]
    # The tables already existed, so version 1 is only recorded; version 2 alters the table.
    assert statuses == [("create_event_tables", "recorded"), ("add_report_digest", "applied")]

    legacy_event = store.page(limit=10, scope=SCOPE)["events"][0]
    assert legacy_event["id"] == "legacy-event"
    assert legacy_event["score"] == 70
    assert legacy_event["report_digest"] is None
    assert store.ingestion_cursor(SCOPE) == "legacy-cursor"


def test_untracked_current_database_is_adopted_without_schema_changes(tmp_path, caplog):
    # A file written by the release before version tracking already has
    # report_digest; adopting it must only add history rows.
    path = tmp_path / "events.sqlite3"
    _create_pre_digest_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE flag_events ADD COLUMN report_digest TEXT")
        connection.execute("UPDATE flag_events SET report_digest = ?", ("ab" * 32,))

    with caplog.at_level(logging.INFO, logger="sentinel.event_store"):
        store = EventStore(str(path))

    assert {record.status for record in _schema_records(caplog)} == {"recorded"}
    assert len(_history(path)) == LATEST_SCHEMA_VERSION
    assert store.page(limit=10, scope=SCOPE)["events"][0]["report_digest"] == "ab" * 32


def test_reopening_a_current_database_changes_nothing(tmp_path, caplog):
    path = tmp_path / "events.sqlite3"
    EventStore(str(path)).save_page([_event("event-1", 10)], "cursor-1", SCOPE)
    history = _history(path)

    with caplog.at_level(logging.INFO, logger="sentinel.event_store"):
        reopened = EventStore(str(path))

    assert _history(path) == history
    assert _schema_records(caplog) == []
    assert reopened.ingestion_cursor(SCOPE) == "cursor-1"


# -- Newer schemas and rollback --------------------------------------------------


def test_newer_schema_is_refused_by_default_and_left_untouched(tmp_path, caplog):
    path = tmp_path / "events.sqlite3"
    _create_newer_database(path)
    history, schema = _history(path), _schema(path)

    with caplog.at_level(logging.INFO, logger="sentinel.event_store"):
        with pytest.raises(EventStoreSchemaError) as error:
            EventStore(str(path))

    assert error.value.reason == "newer_schema"
    assert "EVENT_STORE_ALLOW_NEWER_SCHEMA" in str(error.value)
    assert _history(path) == history
    assert _schema(path) == schema
    record = _schema_records(caplog)[-1]
    assert (record.levelno, record.status, record.reason) == (logging.ERROR, "rejected", "newer_schema")
    assert (record.database_version, record.supported_version) == (LATEST_SCHEMA_VERSION + 1, LATEST_SCHEMA_VERSION)
    assert str(path) not in caplog.text


def test_rollback_opt_in_keeps_reading_and_writing_a_newer_schema(tmp_path, caplog):
    path = tmp_path / "events.sqlite3"
    _create_newer_database(path)
    history = _history(path)

    with caplog.at_level(logging.INFO, logger="sentinel.event_store"):
        store = EventStore(str(path), allow_newer_schema=True)

    record = _schema_records(caplog)[-1]
    assert (record.levelno, record.status, record.reason) == (logging.WARNING, "allowed", "newer_schema")
    store.save_page([_event("event-2", 11, report_digest="cd" * 32)], "cursor-2", SCOPE, expected_cursor="cursor-1")
    assert [event["id"] for event in store.page(limit=10, scope=SCOPE)["events"]] == ["event-2", "event-1"]
    assert store.ingestion_cursor(SCOPE) == "cursor-2"
    assert _history(path) == history

    with sqlite3.connect(path) as connection:
        note = connection.execute("SELECT reviewer_note FROM flag_events WHERE event_id = 'event-2'").fetchone()
    assert note == (None,)


def test_rollback_opt_in_is_read_from_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_STORE_ALLOW_NEWER_SCHEMA", "true")
    assert Settings().event_store_allow_newer_schema is True
    monkeypatch.delenv("EVENT_STORE_ALLOW_NEWER_SCHEMA")
    assert Settings(_env_file=None).event_store_allow_newer_schema is False

    path = tmp_path / "events.sqlite3"
    _create_newer_database(path)
    monkeypatch.setattr(event_store, "get_settings", lambda: Settings(event_store_allow_newer_schema=True))

    assert event_store.get_event_store(str(path)).allow_newer_schema is True


# -- Inconsistent history and failed migrations ----------------------------------


def test_renamed_history_entry_is_rejected(tmp_path):
    path = tmp_path / "events.sqlite3"
    EventStore(str(path))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE schema_migrations SET name = 'something_else' WHERE version = 2")

    with pytest.raises(EventStoreSchemaError) as error:
        EventStore(str(path))

    assert error.value.reason == "history_mismatch"


def test_history_with_a_gap_is_rejected(tmp_path):
    path = tmp_path / "events.sqlite3"
    EventStore(str(path))
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 1")

    with pytest.raises(EventStoreSchemaError) as error:
        EventStore(str(path))

    assert error.value.reason == "history_mismatch"
    assert [version for version, _, _ in _history(path)] == [2]


def test_failed_migration_rolls_back_the_whole_upgrade(tmp_path, caplog):
    # A malformed legacy table without a ledger column cannot take the paging
    # index, so version 1 fails after ingestion_state was already created.
    path = tmp_path / "broken.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE flag_events (scope TEXT, event_id TEXT, created_at TEXT)")
        connection.execute("INSERT INTO flag_events VALUES (?, ?, ?)", (SCOPE, "kept-event", "ledger-1"))

    with caplog.at_level(logging.INFO, logger="sentinel.event_store"):
        with pytest.raises(EventStoreSchemaError) as error:
            EventStore(str(path))

    assert error.value.reason == "migration_failed"
    assert isinstance(error.value.__cause__, sqlite3.OperationalError)
    assert _table_names(path) == {"flag_events"}
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM flag_events").fetchall() == [(SCOPE, "kept-event", "ledger-1")]

    record = _schema_records(caplog)[-1]
    assert (record.migration, record.status, record.error) == ("create_event_tables", "failed", "OperationalError")
    assert not any(record.status == "applied" for record in _schema_records(caplog))
    assert str(path) not in caplog.text


def test_later_migration_failure_keeps_earlier_versions_unapplied(monkeypatch, tmp_path):
    path = tmp_path / "events.sqlite3"
    failing = MIGRATIONS + (
        Migration(LATEST_SCHEMA_VERSION + 1, "add_to_missing_table", (AddColumn("no_such_table", "note", "TEXT"),)),
    )
    monkeypatch.setattr(event_store, "MIGRATIONS", failing)
    monkeypatch.setattr(event_store, "LATEST_SCHEMA_VERSION", len(failing))

    with pytest.raises(EventStoreSchemaError) as error:
        EventStore(str(path))

    assert error.value.reason == "migration_failed"
    assert _table_names(path) == set()


def test_unreadable_file_fails_without_being_overwritten(tmp_path, caplog):
    path = tmp_path / "events.sqlite3"
    contents = b"this is not a sqlite database\n" * 200
    path.write_bytes(contents)

    with caplog.at_level(logging.INFO, logger="sentinel.event_store"):
        with pytest.raises(EventStoreSchemaError) as error:
            EventStore(str(path))

    assert error.value.reason == "storage_unavailable"
    assert path.read_bytes() == contents
    record = _schema_records(caplog)[-1]
    assert (record.status, record.reason) == ("failed", "storage_unavailable")
    assert str(path) not in caplog.text


def test_lock_held_past_the_timeout_fails_then_recovers(monkeypatch, tmp_path):
    path = tmp_path / "events.sqlite3"
    EventStore(str(path))
    monkeypatch.setattr(EventStore, "busy_timeout_seconds", 0.05)

    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(EventStoreSchemaError) as error:
            EventStore(str(path))
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert error.value.reason == "storage_unavailable"
    assert isinstance(error.value.__cause__, sqlite3.OperationalError)
    assert EventStore(str(path)).ingestion_cursor(SCOPE) is None


def test_failed_open_is_not_cached(tmp_path):
    path = tmp_path / "events.sqlite3"
    path.write_bytes(b"not a database" * 100)
    with pytest.raises(EventStoreSchemaError):
        event_store.get_event_store(str(path))

    path.unlink()
    assert event_store.get_event_store(str(path)).ingestion_cursor(SCOPE) is None


# -- Catalog rules ----------------------------------------------------------------


def test_released_catalog_is_contiguous_and_additive():
    assert _validate_catalog(MIGRATIONS) == LATEST_SCHEMA_VERSION == 2
    step_types = {type(step).__name__ for migration in MIGRATIONS for step in migration.steps}
    assert step_types <= {"CreateTable", "CreateIndex", "AddColumn"}


@pytest.mark.parametrize("catalog, message", [
    ((Migration(1, "first", ()), Migration(3, "third", ())), "numbered 1..N"),
    ((Migration(2, "second", ()), Migration(1, "first", ())), "numbered 1..N"),
    ((Migration(1, "same", ()), Migration(2, "same", ())), "unique"),
    ((Migration(1, "Not-An-Identifier", ()),), "invalid schema identifier"),
])
def test_catalog_validation_rejects_malformed_catalogs(catalog, message):
    with pytest.raises(ValueError, match=message):
        _validate_catalog(catalog)


@pytest.mark.parametrize("declaration, message", [
    ("TEXT NOT NULL", "nullable or declare a DEFAULT"),
    ("TEXT UNIQUE", "cannot change table keys"),
    ("INTEGER PRIMARY KEY", "cannot change table keys"),
])
def test_added_columns_must_stay_compatible_with_older_writers(declaration, message):
    with pytest.raises(ValueError, match=message):
        AddColumn("flag_events", "note", declaration)


def test_step_identifiers_cannot_carry_sql():
    with pytest.raises(ValueError, match="invalid schema identifier"):
        AddColumn("flag_events; DROP TABLE flag_events", "note", "TEXT")
    with pytest.raises(ValueError, match="invalid schema identifier"):
        CreateTable("events--", "id TEXT")
    assert AddColumn("flag_events", "note", "INTEGER NOT NULL DEFAULT 0").column == "note"


# -- Application startup and status -------------------------------------------------


@pytest.fixture
def startup(monkeypatch):
    async def idle():
        return None

    def configure(path):
        monkeypatch.setattr(main, "settings", Settings(contract_id="C" + "A" * 55, event_store_path=str(path)))
        monkeypatch.setattr(main, "_ingest_events_forever", idle)
        monkeypatch.setattr(main, "_run_agent_forever", idle)

    return configure


def test_startup_migrates_the_event_store_before_serving(startup, tmp_path):
    path = tmp_path / "startup.sqlite3"
    startup(path)

    with TestClient(main.app) as client:
        assert client.get("/live").status_code == 200
        assert len(_history(path)) == LATEST_SCHEMA_VERSION


def test_startup_fails_when_the_schema_cannot_be_migrated(startup, tmp_path):
    path = tmp_path / "newer.sqlite3"
    _create_newer_database(path)
    startup(path)

    with pytest.raises(EventStoreSchemaError) as error:
        with TestClient(main.app):
            pass

    assert error.value.reason == "newer_schema"
    assert stellar._http_client is None


def test_startup_does_not_create_a_store_without_a_contract(monkeypatch, tmp_path):
    path = tmp_path / "unused.sqlite3"
    monkeypatch.setattr(main, "settings", Settings(contract_id="", event_store_path=str(path)))

    asyncio.run(_enter_lifespan())

    assert not path.exists()


async def _enter_lifespan():
    async with main.lifespan(main.app):
        pass


def test_ingestion_status_reports_unavailable_store_on_schema_error(monkeypatch):
    def refuse(_path):
        raise EventStoreSchemaError("newer_schema", "event store schema version 3 is newer than supported version 2")

    monkeypatch.setattr(events_router, "get_settings", lambda: Settings(contract_id="C" + "A" * 55))
    monkeypatch.setattr(events_router, "get_event_store", refuse)

    response = TestClient(main.app).get("/events/ingestion")

    assert response.status_code == 200
    assert response.json()["store"] == "unavailable"
    assert response.json()["cursor"] is None
