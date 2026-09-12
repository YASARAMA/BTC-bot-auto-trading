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


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())
    # ccxt is chatty at DEBUG; keep it quiet unless explicitly wanted.
    logging.getLogger("ccxt").setLevel(max(logging.INFO, root.level))
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one structured line. Fields land in the JSON payload as top-level keys."""
    payload = {"event": event, **fields}
    logger.log(level, event, extra={"event": payload})
