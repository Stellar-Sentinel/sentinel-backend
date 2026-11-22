import sqlite3

from app.event_store import EventStore


def test_legacy_digest_migration_emits_bounded_diagnostic(tmp_path, caplog):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE flag_events (scope TEXT, event_id TEXT, ledger INTEGER)"
        )

    with caplog.at_level("INFO", logger="sentinel.event_store"):
        EventStore(str(path))

    record = next(record for record in caplog.records if record.migration == "add_report_digest")
    assert record.status == "applied"
    assert record.getMessage() == "event_store_schema_migration migration=add_report_digest status=applied"
    assert str(path) not in caplog.text


def test_migrated_store_does_not_repeat_migration_log(tmp_path, caplog):
    path = tmp_path / "events.sqlite3"
    EventStore(str(path))

    with caplog.at_level("INFO", logger="sentinel.event_store"):
        EventStore(str(path))

    assert not any(record.name == "sentinel.event_store" for record in caplog.records)
