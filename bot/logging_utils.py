"""Structured logging: one JSON object per line, or plain text for humans."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "event", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    """Human format: the message followed by the event's fields as key=value."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extra = getattr(record, "event", None)
        if isinstance(extra, dict):
            fields = " ".join(f"{k}={json.dumps(v, default=str)}" for k, v in extra.items() if k != "event")
            if fields:
                base = f"{base} {fields}"
        return base


def setup_logging(level: str = "INFO", fmt: str = "json", *, stdout: bool = True,
                  file_path: str | None = None, extra_handlers: list[logging.Handler] | None = None) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    text = TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if stdout and sys.stdout is not None:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter() if fmt == "json" else text)
        root.addHandler(handler)
    if file_path:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(file_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(text)
        root.addHandler(fh)
    for h in extra_handlers or []:
        root.addHandler(h)
    root.setLevel(level.upper())
    # ccxt is chatty at DEBUG; keep it quiet unless explicitly wanted.
    logging.getLogger("ccxt").setLevel(max(logging.INFO, root.level))
    logging.getLogger("urllib3").setLevel(logging.WARNING)


IMPORTANT_EVENTS = frozenset({
    "startup", "ui_start", "shutdown", "reconcile", "order_filled", "order_partial_fill",
    "order_not_filled", "position_opened", "trade_closed", "risk_event", "cycle_error",
    "protective_exit_blocked", "manual_order", "levels_changed", "kill_switch_on",
    "kill_switch_off", "candle_gap", "candles_missed", "live_mode", "replay_finished",
    "update_available", "update_installed", "startup_refused", "ai_decision", "mode_changed",
})


def channel_for(item: dict[str, Any]) -> str:
    """Which channel a log record belongs to: 'log' (what happened) or 'debug' (everything)."""
    if item.get("level") in ("WARNING", "ERROR", "CRITICAL"):
        return "log"
    return "log" if item.get("event") in IMPORTANT_EVENTS else "debug"


class EventBufferHandler(logging.Handler):
    """Keeps the last N log records as dicts so a UI can poll them."""

    def __init__(self, maxlen: int = 2000) -> None:
        super().__init__()
        from collections import deque
        from threading import Lock

        self.buffer: "deque[dict[str, Any]]" = deque(maxlen=maxlen)
        self._lock = Lock()
        self._next_id = 1
        self.listeners: list[Any] = []  # callables(record_dict)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            item: dict[str, Any] = {
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            extra = getattr(record, "event", None)
            if isinstance(extra, dict):
                item.update(extra)
            if record.exc_info:
                item["exc"] = self.formatException(record.exc_info) if hasattr(self, "formatException") else str(record.exc_info[1])
            item["channel"] = channel_for(item)
            with self._lock:
                item["id"] = self._next_id
                self._next_id += 1
                self.buffer.append(item)
            for fn in list(self.listeners):
                try:
                    fn(item)
                except Exception:  # noqa: BLE001 - a UI listener must never break logging
                    pass
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def since(self, last_id: int, limit: int = 500, channel: str | None = None) -> list[dict[str, Any]]:
        """Records newer than last_id. channel 'log' or 'debug' filters; None returns both.

        The OLDEST matching records come first. Returning the newest instead would let a
        burst larger than `limit` push the caller's cursor past everything in between, and
        those lines would never be shown - exactly when the log matters most.
        """
        with self._lock:
            items = [x for x in self.buffer if x["id"] > last_id]
        if channel in ("log", "debug"):
            items = [x for x in items if x.get("channel") == channel]
        return items[:limit]


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one structured line. Fields land in the JSON payload as top-level keys."""
    payload = {"event": event, **fields}
    logger.log(level, event, extra={"event": payload})
