"""SQLite access shared by every component.

One connection per database file, serialised by a re-entrant lock. SQLite allows one
writer at a time anyway, and a single connection used from the API threads, the paper
trading loop and the background jobs is only safe when every use is serialised: an
unlocked connection shared across threads interleaves statements and loses writes.

Transactions nest: the outermost ``transaction()`` is a real ``BEGIN IMMEDIATE``, inner
ones are savepoints, so a helper that needs atomicity can open one without knowing whether
its caller already did.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from app.core.clock import now_ms


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    sql: str = ""
    apply: Callable[[sqlite3.Connection], None] | None = None


def to_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def from_json(text: str | None, default: Any = None) -> Any:
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


class Database:
    def __init__(self, path: str | Path, migrations: Sequence[Migration], *, name: str = "database") -> None:
        self.name = name
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self._migrations = sorted(migrations, key=lambda m: m.version)
        self.migrate()

    # ----- schema --------------------------------------------------------------------
    def migrate(self) -> int:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                " version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at INTEGER NOT NULL)")
            current = self.scalar("SELECT COALESCE(MAX(version), 0) FROM schema_version") or 0
            for m in self._migrations:
                if m.version <= current:
                    continue
                with self.transaction():
                    if m.sql:
                        for statement in _split_sql(m.sql):
                            self._conn.execute(statement)
                    if m.apply is not None:
                        m.apply(self._conn)
                    self._conn.execute(
                        "INSERT INTO schema_version(version, description, applied_at) VALUES (?, ?, ?)",
                        (m.version, m.description, now_ms()))
                current = m.version
            return int(current)

    @property
    def schema_version(self) -> int:
        return int(self.scalar("SELECT COALESCE(MAX(version), 0) FROM schema_version") or 0)

    # ----- transactions --------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        with self._lock:
            savepoint = f"sp_{self._depth}"
            if self._depth == 0:
                self._conn.execute("BEGIN IMMEDIATE")
            else:
                self._conn.execute(f"SAVEPOINT {savepoint}")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("ROLLBACK")
                else:
                    self._conn.execute(f"ROLLBACK TO {savepoint}")
                    self._conn.execute(f"RELEASE {savepoint}")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("COMMIT")
                else:
                    self._conn.execute(f"RELEASE {savepoint}")

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    # ----- statements ----------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        if not rows:
            return 0
        with self.transaction():
            cur = self._conn.executemany(sql, rows)
            return cur.rowcount

    def insert(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return int(cur.lastrowid or 0)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def scalar(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return row[0] if row is not None else None

    def raw(self) -> sqlite3.Connection:
        """The underlying connection, for pandas. Hold ``lock`` while using it."""
        return self._conn

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    # ----- small key/value store for flags and checkpoints ---------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM kv_state WHERE key = ?", (key,))
        return from_json(row["value"], default) if row else default

    def set_state(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv_state(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, to_json(value), now_ms()))

    def delete_state(self, key: str) -> None:
        self.execute("DELETE FROM kv_state WHERE key = ?", (key,))

    # ----- housekeeping ----------------------------------------------------------------
    def integrity_check(self) -> str:
        return str(self.scalar("PRAGMA quick_check") or "unknown")

    def size_bytes(self) -> int:
        if self.path == ":memory:":
            return 0
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.path + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


def _split_sql(script: str) -> list[str]:
    """Split a migration script into statements. Migrations contain no semicolons in strings."""
    return [s.strip() for s in script.split(";") if s.strip()]
