"""SQLite-backed idempotent storage for indexed Soroban flag events."""
import base64
import json
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException


class EventStore:
    def __init__(self, path: str):
        if path == ":memory:":
            raise ValueError("event store requires a persistent SQLite file path")
        self.path = path
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS flag_events (
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
                """CREATE TABLE IF NOT EXISTS ingestion_state (
                    scope TEXT PRIMARY KEY,
                    cursor TEXT
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_flag_events_page ON flag_events(scope, ledger DESC, event_id DESC)"
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def ingestion_cursor(self, scope: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM ingestion_state WHERE scope = ?", (scope,)
            ).fetchone()
        return row["cursor"] if row else None

    def save_page(self, events: list[dict], next_cursor: str | None, scope: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for event in events:
                event_id = event.get("id")
                ledger = event.get("ledger")
                if event_id is None or ledger is None:
                    raise ValueError("Soroban flag event is missing its ID or ledger")
                try:
                    ledger = int(ledger)
                except (TypeError, ValueError):
                    raise ValueError("Soroban flag event has an invalid ledger") from None
                connection.execute(
                    """INSERT OR IGNORE INTO flag_events
                    (scope, event_id, ledger, created_at, agent, subject, score_json, contract_id, tx_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scope,
                        str(event_id),
                        ledger,
                        event.get("created_at"),
                        event.get("agent"),
                        event.get("subject"),
                        json.dumps(event.get("score")),
                        event.get("contract_id"),
                        event.get("tx_hash"),
                    ),
                )
            if next_cursor:
                connection.execute(
                    """INSERT INTO ingestion_state(scope, cursor) VALUES (?, ?)
                    ON CONFLICT(scope) DO UPDATE SET cursor = excluded.cursor""",
                    (scope, next_cursor),
                )
            connection.commit()

    def page(self, limit: int, scope: str, cursor: str | None = None) -> dict:
        after = self._decode_cursor(cursor) if cursor else None
        with self._connect() as connection:
            if after:
                rows = connection.execute(
                    """SELECT * FROM flag_events
                    WHERE scope = ? AND (ledger < ? OR (ledger = ? AND event_id < ?))
                    ORDER BY ledger DESC, event_id DESC LIMIT ?""",
                    (scope, after[0], after[0], after[1], limit + 1),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM flag_events WHERE scope = ? ORDER BY ledger DESC, event_id DESC LIMIT ?",
                    (scope, limit + 1),
                ).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        next_cursor = self._encode_cursor(selected[-1]) if has_more and selected else None
        return {
            "events": [
                {
                    "id": row["event_id"],
                    "ledger": row["ledger"],
                    "created_at": row["created_at"],
                    "agent": row["agent"],
                    "subject": row["subject"],
                    "score": json.loads(row["score_json"]),
                    "contract_id": row["contract_id"],
                    "tx_hash": row["tx_hash"],
                }
                for row in selected
            ],
            "next_cursor": next_cursor,
        }

    @staticmethod
    def _encode_cursor(row) -> str:
        value = f"{row['ledger']}\n{row['event_id']}".encode("utf-8")
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[int, str]:
        try:
            padding = "=" * (-len(cursor) % 4)
            value = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
            ledger, event_id = value.split("\n", 1)
            return int(ledger), event_id
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Invalid event cursor") from exc


@lru_cache
def get_event_store(path: str) -> EventStore:
    return EventStore(path)
