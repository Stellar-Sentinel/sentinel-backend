"""SQLite-backed idempotent storage for indexed Soroban flag events.

The schema evolves only through the versioned, additive migrations in
``MIGRATIONS``; docs/EVENT_STORE_MIGRATION_OPERATIONS.md describes the rules,
rollback expectations, and startup failure behavior.
"""
import base64
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings

logger = logging.getLogger("sentinel.event_store")

# Sentinel for save_page callers that do not need a cursor precondition.
_ANY_CURSOR = object()

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")

_CREATE_HISTORY_TABLE = """CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
)"""


class CursorConflictError(RuntimeError):
    """The stored ingestion cursor moved since the caller last read it.

    Raised instead of writing so that a slower, concurrent sync (another
    worker process sharing the database) can never move the cursor backwards.
    """


class EventStoreSchemaError(RuntimeError):
    """The database cannot be brought to a schema this release supports.

    ``reason`` is a fixed label (``newer_schema``, ``history_mismatch``,
    ``migration_failed`` or ``storage_unavailable``) that is safe to log. The
    message never contains the database path or stored event data.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _check_identifier(value: str) -> str:
    # Steps interpolate table, column, and index names into DDL, so only plain
    # lowercase identifiers are accepted. None of them come from requests.
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid schema identifier: {value!r}")
    return value


def _schema_object_exists(connection, kind: str, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)
    ).fetchone()
    return row is not None


# Migration steps. Each one is additive and idempotent: it inspects the schema,
# returns False when its object already exists, and never drops, renames, or
# rewrites existing tables, columns, or rows. There is deliberately no step type
# for destructive changes.

@dataclass(frozen=True)
class CreateTable:
    name: str
    columns: str

    def __post_init__(self):
        _check_identifier(self.name)

    def apply(self, connection) -> bool:
        if _schema_object_exists(connection, "table", self.name):
            return False
        connection.execute(f"CREATE TABLE {self.name} ({self.columns})")
        return True


@dataclass(frozen=True)
class CreateIndex:
    name: str
    table: str
    columns: str

    def __post_init__(self):
        _check_identifier(self.name)
        _check_identifier(self.table)

    def apply(self, connection) -> bool:
        if _schema_object_exists(connection, "index", self.name):
            return False
        connection.execute(f"CREATE INDEX {self.name} ON {self.table}({self.columns})")
        return True


@dataclass(frozen=True)
class AddColumn:
    """Add a column that existing rows can hold without being rewritten."""

    table: str
    column: str
    declaration: str

    def __post_init__(self):
        _check_identifier(self.table)
        _check_identifier(self.column)
        declaration = self.declaration.upper()
        # Older releases keep inserting rows without this column after a code
        # rollback, so it must accept NULL or supply a constant default.
        if "NOT NULL" in declaration and "DEFAULT" not in declaration:
            raise ValueError("an added column must be nullable or declare a DEFAULT")
        if "PRIMARY KEY" in declaration or "UNIQUE" in declaration:
            raise ValueError("an added column cannot change table keys")

    def apply(self, connection) -> bool:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({self.table})")}
        if self.column in columns:
            return False
        connection.execute(f"ALTER TABLE {self.table} ADD COLUMN {self.column} {self.declaration}")
        return True


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    steps: tuple

    def apply(self, connection) -> bool:
        """Run every step; True when at least one of them changed the schema."""
        changed = [step.apply(connection) for step in self.steps]
        return any(changed)


# Append-only. A released migration is never edited, reordered, or removed:
# databases record its version and name, and a mismatch stops startup.
MIGRATIONS = (
    Migration(1, "create_event_tables", (
        CreateTable("flag_events", """
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
        """),
        CreateTable("ingestion_state", "scope TEXT PRIMARY KEY, cursor TEXT"),
        CreateIndex("idx_flag_events_page", "flag_events", "scope, ledger DESC, event_id DESC"),
    )),
    # flaggedv2 events carry a report digest; rows indexed earlier keep NULL.
    Migration(2, "add_report_digest", (
        AddColumn("flag_events", "report_digest", "TEXT"),
    )),
    Migration(3, "add_agent_cooldowns", (
        CreateTable("agent_cooldowns", """
            scope TEXT NOT NULL,
            address TEXT NOT NULL,
            flagged_at REAL NOT NULL,
            PRIMARY KEY (scope, address)
        """),
    )),
)


def _validate_catalog(migrations) -> int:
    """Return the latest schema version after checking the catalog's shape."""
    if [migration.version for migration in migrations] != list(range(1, len(migrations) + 1)):
        raise ValueError("event store migrations must be numbered 1..N in order")
    names = [_check_identifier(migration.name) for migration in migrations]
    if len(set(names)) != len(names):
        raise ValueError("event store migration names must be unique")
    return len(migrations)


LATEST_SCHEMA_VERSION = _validate_catalog(MIGRATIONS)


def _database_version(rows) -> int:
    """Validate recorded history against the catalog and return its version.

    History must be a gap-free run starting at 1, and every version this
    release knows must carry the same name. Versions above the catalog belong
    to a newer release and are judged by the caller.
    """
    recorded = {row["version"]: row["name"] for row in rows}
    versions = sorted(recorded)
    if versions != list(range(1, len(versions) + 1)):
        raise EventStoreSchemaError("history_mismatch", "event store schema history has gaps")
    for migration in MIGRATIONS:
        if migration.version in recorded and recorded[migration.version] != migration.name:
            raise EventStoreSchemaError(
                "history_mismatch",
                f"event store schema version {migration.version} does not match this release",
            )
    return versions[-1] if versions else 0


def _log_schema_check(level: int, status: str, reason: str, database_version: int | None = None) -> None:
    logger.log(
        level,
        "event_store_schema_check status=%s reason=%s database_version=%s supported_version=%s",
        status, reason, database_version, LATEST_SCHEMA_VERSION,
        extra={"status": status, "reason": reason, "database_version": database_version,
               "supported_version": LATEST_SCHEMA_VERSION},
    )


class EventStore:
    # Seconds to wait for another process's write lock, both while migrating
    # at startup and on every later write.
    busy_timeout_seconds = 10

    def __init__(self, path: str, *, allow_newer_schema: bool = False):
        if path == ":memory:":
            raise ValueError("event store requires a persistent SQLite file path")
        self.path = path
        self.allow_newer_schema = allow_newer_schema
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                self._migrate(connection)
        except sqlite3.Error as exc:
            # Errors inside a migration are already converted; this covers an
            # unreadable file, a lock held past the timeout, or a broken history table.
            _log_schema_check(logging.ERROR, "failed", "storage_unavailable")
            raise EventStoreSchemaError(
                "storage_unavailable", "event store could not be opened to check its schema"
            ) from exc

    def _migrate(self, connection) -> None:
        """Bring the file to LATEST_SCHEMA_VERSION or raise EventStoreSchemaError.

        One IMMEDIATE transaction covers the history read and every pending
        migration, so concurrent starters serialize on the write lock and a
        failure rolls the whole upgrade back, leaving the file at its old version.
        """
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_CREATE_HISTORY_TABLE)
        rows = connection.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
        try:
            database_version = _database_version(rows)
        except EventStoreSchemaError as exc:
            _log_schema_check(logging.ERROR, "rejected", exc.reason)
            raise

        if database_version > LATEST_SCHEMA_VERSION:
            if not self.allow_newer_schema:
                _log_schema_check(logging.ERROR, "rejected", "newer_schema", database_version)
                raise EventStoreSchemaError(
                    "newer_schema",
                    f"event store schema version {database_version} is newer than supported "
                    f"version {LATEST_SCHEMA_VERSION}; set EVENT_STORE_ALLOW_NEWER_SCHEMA=true "
                    "only for a deliberate code rollback",
                )
            _log_schema_check(logging.WARNING, "allowed", "newer_schema", database_version)

        applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        outcomes = []
        for migration in MIGRATIONS[database_version:]:
            try:
                changed = migration.apply(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, applied_at),
                )
            except sqlite3.Error as exc:
                logger.error(
                    "event_store_schema_migration migration=%s status=failed error=%s",
                    migration.name, type(exc).__name__,
                    extra={"migration": migration.name, "status": "failed",
                           "schema_version": migration.version, "error": type(exc).__name__},
                )
                raise EventStoreSchemaError(
                    "migration_failed",
                    f"event store migration {migration.name} failed; no schema changes were kept",
                ) from exc
            # "recorded" means the objects already existed (a database created
            # before version tracking) and only the history row was written.
            outcomes.append((migration, "applied" if changed else "recorded"))
        connection.commit()

        # Logged after commit so "applied" never describes a rolled-back change.
        for migration, status in outcomes:
            logger.info(
                "event_store_schema_migration migration=%s status=%s",
                migration.name, status,
                extra={"migration": migration.name, "status": status, "schema_version": migration.version},
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_seconds)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def ingestion_cursor(self, scope: str) -> str | None:
        with self._connect() as connection:
            return self._read_cursor(connection, scope)

    def agent_cooldown(self, scope: str, address: str) -> float | None:
        """Return a persisted cooldown timestamp, if one exists."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT flagged_at FROM agent_cooldowns WHERE scope = ? AND address = ?",
                (scope, address),
            ).fetchone()
        return float(row["flagged_at"]) if row else None

    def save_agent_cooldown(self, scope: str, address: str, flagged_at: float) -> None:
        """Persist the time an address was most recently flagged."""
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_cooldowns(scope, address, flagged_at) VALUES (?, ?, ?)
                ON CONFLICT(scope, address) DO UPDATE SET flagged_at = excluded.flagged_at""",
                (scope, address, flagged_at),
            )

    def clear_agent_cooldown(self, scope: str, address: str) -> None:
        """Remove a persisted cooldown for an address."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM agent_cooldowns WHERE scope = ? AND address = ?",
                (scope, address),
            )

    @staticmethod
    def _read_cursor(connection, scope: str) -> str | None:
        row = connection.execute(
            "SELECT cursor FROM ingestion_state WHERE scope = ?", (scope,)
        ).fetchone()
        return row["cursor"] if row else None

    def reset_ingestion_cursor(self, scope: str, expected_cursor: str) -> bool:
        """Forget the scope's cursor only if it still equals ``expected_cursor``.

        Indexed events are kept. Returns False when another sync already
        replaced the cursor, in which case nothing is changed.
        """
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM ingestion_state WHERE scope = ? AND cursor = ?",
                (scope, expected_cursor),
            ).rowcount
        return deleted == 1

    def save_page(self, events: list[dict], next_cursor: str | None, scope: str,
                  expected_cursor=_ANY_CURSOR) -> None:
        """Insert a page of events and advance the cursor in one transaction.

        When ``expected_cursor`` is given (``None`` meaning "no cursor stored
        yet"), the write is a compare-and-set: if the stored cursor differs,
        nothing is written and CursorConflictError is raised.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if expected_cursor is not _ANY_CURSOR:
                stored = self._read_cursor(connection, scope)
                if stored != expected_cursor:
                    raise CursorConflictError("ingestion cursor for scope changed concurrently")
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
                    (scope, event_id, ledger, created_at, agent, subject, score_json, contract_id, tx_hash, report_digest)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        event.get("report_digest"),
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
                    "report_digest": row["report_digest"],
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
    """Return the process-wide store for ``path``, migrating it on first use.

    A failed open raises EventStoreSchemaError and is not cached, so the next
    call retries instead of reusing a half-initialized store.
    """
    return EventStore(path, allow_newer_schema=get_settings().event_store_allow_newer_schema)
