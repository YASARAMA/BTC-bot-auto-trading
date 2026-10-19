"""Time helpers. Every timestamp in this program is UTC milliseconds since the epoch."""
from __future__ import annotations

import time
from datetime import datetime, timezone

DAY_MS = 86_400_000


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str | int | float | None) -> int | None:
    """Accept milliseconds, seconds, or an ISO date/time; return UTC milliseconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = int(value)
        return v * 1000 if v < 100_000_000_000 else v
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return parse_ts(int(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def utc_day_start(ts_ms: int) -> int:
    return ts_ms - ts_ms % DAY_MS


def utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def utc_month(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")
