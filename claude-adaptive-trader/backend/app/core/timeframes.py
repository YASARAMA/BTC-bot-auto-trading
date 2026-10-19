"""Candle timeframes.

A candle is identified by its *open* time. It is complete (closed) once its close time,
``open + duration``, has passed. Strategy decisions are only ever taken on closed candles,
and the earliest moment a decision on candle ``t`` can act is the open of candle ``t + 1``.
"""
from __future__ import annotations

from app.core.errors import InvalidRequest

TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def tf_ms(timeframe: str) -> int:
    try:
        return TIMEFRAME_MS[timeframe]
    except KeyError:
        raise InvalidRequest(
            f"unsupported timeframe {timeframe!r}; use one of {', '.join(TIMEFRAME_MS)}") from None


def validate_timeframe(timeframe: str) -> str:
    tf_ms(timeframe)
    return timeframe


def floor_ts(ts_ms: int, timeframe: str) -> int:
    step = tf_ms(timeframe)
    return ts_ms - ts_ms % step


def is_aligned(ts_ms: int, timeframe: str) -> bool:
    return ts_ms % tf_ms(timeframe) == 0


def close_time(open_ts: int, timeframe: str) -> int:
    return open_ts + tf_ms(timeframe)


def last_closed_open(now: int, timeframe: str) -> int:
    """Open time of the most recent candle that has fully closed at ``now``."""
    return floor_ts(now, timeframe) - tf_ms(timeframe)


def is_closed(open_ts: int, timeframe: str, now: int) -> bool:
    return open_ts + tf_ms(timeframe) <= now


def can_derive(base: str, target: str) -> bool:
    """Whether ``target`` candles can be built from complete groups of ``base`` candles."""
    b, t = tf_ms(base), tf_ms(target)
    return t > b and t % b == 0


def ordered(timeframes: list[str]) -> list[str]:
    return sorted(set(timeframes), key=tf_ms)
