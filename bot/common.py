"""Small shared helpers: timeframes, UTC time, retry with backoff."""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

_UNIT_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def timeframe_to_ms(timeframe: str) -> int:
    """Convert a ccxt style timeframe ('1m', '4h', '1d') to milliseconds."""
    tf = timeframe.strip().lower()
    if len(tf) < 2 or tf[-1] not in _UNIT_MS or not tf[:-1].isdigit():
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    return int(tf[:-1]) * _UNIT_MS[tf[-1]]


def now_ms() -> int:
    return int(time.time() * 1000)


def floor_ts(ts_ms: int, step_ms: int) -> int:
    return ts_ms - (ts_ms % step_ms)


def utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def next_utc_midnight(ts_ms: int) -> int:
    return floor_ts(ts_ms, _UNIT_MS["d"]) + _UNIT_MS["d"]


def iso(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_date_ms(text: str) -> int:
    """Parse 'YYYY-MM-DD' or an ISO timestamp (UTC) to epoch milliseconds."""
    txt = text.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    dt = datetime.fromisoformat(txt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def retry_call(
    fn: Callable[[], T],
    *,
    retries: int,
    base_seconds: float,
    max_seconds: float,
    exceptions: Iterable[type[BaseException]] = (Exception,),
    logger: logging.Logger | None = None,
    what: str = "call",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call fn, retrying on the given exceptions with exponential backoff and jitter."""
    exc_types = tuple(exceptions)
    attempt = 0
    while True:
        try:
            return fn()
        except exc_types as exc:  # noqa: PERF203
            attempt += 1
            if attempt > retries:
                raise
            delay = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
            delay = delay * (0.5 + random.random() / 2)
            if logger:
                logger.warning(
                    "retrying %s after error (attempt %d/%d, sleeping %.1fs): %s",
                    what, attempt, retries, delay, exc,
                )
            sleep(delay)


def round_step(value: float, step: float | None) -> float:
    """Round a quantity DOWN to the exchange step size (never rounds up an order)."""
    if not step or step <= 0:
        return value
    import math

    units = math.floor((value + 1e-12) / step)
    return round(units * step, 12)
