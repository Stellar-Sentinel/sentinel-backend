import sqlite3

from app.event_store import EventStore


def test_report_digest_migration_preserves_legacy_rows_and_cursor(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE flag_events (
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
        )
        connection.execute(
            "INSERT INTO flag_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("scope", "event-1", 10, "now", "agent", "subject", "90", "contract", "tx"),
        )
        connection.execute("CREATE TABLE ingestion_state (scope TEXT PRIMARY KEY, cursor TEXT)")
        connection.execute("INSERT INTO ingestion_state VALUES (?, ?)", ("scope", "cursor-1"))

    store = EventStore(str(path))
    page = store.page(limit=10, scope="scope")

    assert page["events"][0]["id"] == "event-1"
    assert page["events"][0]["score"] == 90
    assert page["events"][0]["report_digest"] is None
    assert store.ingestion_cursor("scope") == "cursor-1"

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(flag_events)")}
    assert "report_digest" in columns
