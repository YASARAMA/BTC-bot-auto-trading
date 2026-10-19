"""Structured logging: console, an in-memory ring buffer and the ``logs`` table.

Each record carries a ``source`` (which component wrote it) and an optional ``context``
dictionary. Anything that looks like a credential is masked before it is written anywhere,
because a log line is the most common way a secret leaves a machine.

Database writes happen on a background thread: the handler only queues the record, so a
slow disk can never stall the trading loop, and logging from inside a database call can
never deadlock on the database lock.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import traceback
from collections import deque
from typing import Any

from app.core.clock import now_ms
from app.storage.database import Database, to_json

_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}")
_KEY_VALUE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|authorization)(\s*[=:]\s*|\"\s*:\s*\")(?:bearer\s+)?"
    r"(?!bearer\b)([^\s\"',;]{6,})")


def redact(text: str) -> str:
    """Mask anything shaped like a credential. Order matters: the specific shapes go first."""
    out = _ANTHROPIC_KEY.sub("sk-ant-***", text)
    out = _BEARER.sub("Bearer ***", out)
    return _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", out)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: ("***" if _looks_secret_key(str(k)) else _redact_value(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


def _looks_secret_key(key: str) -> bool:
    k = key.lower()
    return any(word in k for word in ("api_key", "apikey", "secret", "password", "token", "authorization"))


class LogBuffer(logging.Handler):
    """Keeps the most recent records in memory for the diagnostics page."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__(level=logging.INFO)
        self.records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self.counts: dict[str, int] = {"WARNING": 0, "ERROR": 0, "CRITICAL": 0}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            row = record_to_row(record)
        except Exception:  # pragma: no cover - logging must never raise
            return
        self.records.append(row)
        if row["level"] in self.counts:
            self.counts[row["level"]] += 1


class DatabaseLogHandler(logging.Handler):
    def __init__(self, db: Database, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self.db = db
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=10_000)
        self._dropped = 0
        self._thread = threading.Thread(target=self._run, name="log-writer", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(record_to_row(record))
        except queue.Full:
            self._dropped += 1
        except Exception:  # pragma: no cover
            pass

    def _run(self) -> None:
        stop = False
        while not stop:
            item = self._queue.get()
            taken = 1
            batch: list[dict[str, Any]] = []
            if item is None:
                stop = True
            else:
                batch.append(item)
            while not stop and len(batch) < 200:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                taken += 1
                if nxt is None:
                    stop = True
                else:
                    batch.append(nxt)
            if batch:
                self._write(batch)
            for _ in range(taken):
                self._queue.task_done()

    def _write(self, batch: list[dict[str, Any]]) -> None:
        try:
            self.db.executemany(
                "INSERT INTO logs(ts, level, source, message, context) VALUES (?, ?, ?, ?, ?)",
                [(r["ts"], r["level"], r["source"], r["message"], to_json(r["context"]) if r["context"] else None)
                 for r in batch])
        except Exception:  # pragma: no cover - the database may already be closed at shutdown
            pass

    def flush(self) -> None:
        self._queue.join()

    def close(self) -> None:
        try:
            self._queue.put(None, timeout=2)
            self._thread.join(timeout=5)
        except Exception:  # pragma: no cover
            pass
        super().close()


def record_to_row(record: logging.LogRecord) -> dict[str, Any]:
    message = record.getMessage()
    context = getattr(record, "context", None)
    if record.exc_info:
        tb = "".join(traceback.format_exception(*record.exc_info))
        context = dict(context or {})
        context["traceback"] = tb[-6000:]
    source = getattr(record, "source", None) or record.name.removeprefix("app.")
    return {
        "ts": int(record.created * 1000) if record.created else now_ms(),
        "level": record.levelname,
        "source": source,
        "message": redact(message),
        "context": _redact_value(context) if context else None,
    }


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_buffer: LogBuffer | None = None
_db_handler: DatabaseLogHandler | None = None


def configure_logging(db: Database | None = None, *, level: int = logging.INFO, console: bool = True) -> LogBuffer:
    """Idempotent: calling it again replaces the database handler (tests open many databases)."""
    global _buffer, _db_handler
    root = logging.getLogger("app")
    root.setLevel(level)
    root.propagate = False
    if _buffer is None:
        _buffer = LogBuffer()
        root.addHandler(_buffer)
        if console:
            handler = logging.StreamHandler()
            handler.setFormatter(_ConsoleFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
            root.addHandler(handler)
    if _db_handler is not None:
        root.removeHandler(_db_handler)
        _db_handler.close()
        _db_handler = None
    if db is not None:
        _db_handler = DatabaseLogHandler(db)
        root.addHandler(_db_handler)
    return _buffer


def shutdown_logging() -> None:
    global _db_handler
    if _db_handler is not None:
        logging.getLogger("app").removeHandler(_db_handler)
        _db_handler.close()
        _db_handler = None


def flush_logs() -> None:
    if _db_handler is not None:
        _db_handler.flush()


def log_buffer() -> LogBuffer:
    return _buffer or configure_logging(console=False)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("app") else f"app.{name}")


def log_event(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    """Log with a structured context dictionary attached."""
    logger.log(level, message, extra={"context": context or None})
