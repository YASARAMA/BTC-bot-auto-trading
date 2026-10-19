"""Test doubles. Everything here exists only for tests and never ships as market data.

``FakeExchange`` mimics the handful of CCXT methods the public client uses, serving a
deterministic synthetic series (a seeded random walk) so that paging, validation, gaps
and failures can be tested without a network.
"""
from __future__ import annotations

from typing import Any

import ccxt
import numpy as np
import pandas as pd

from app.core.timeframes import tf_ms


def synthetic_candles(n: int, timeframe: str = "15m", start: int = 1_700_000_000_000, seed: int = 7,
                      price: float = 30_000.0, vol: float = 0.003, drift: float = 0.0) -> pd.DataFrame:
    """A seeded random walk with internally consistent OHLC. Test fixture only."""
    step = tf_ms(timeframe)
    start = start - start % step
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    closes = price * np.exp(np.cumsum(rets))
    opens = np.concatenate([[price], closes[:-1]])
    spread = np.abs(rng.normal(0, vol / 2, n)) * closes
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    volume = rng.uniform(50, 150, n)
    return pd.DataFrame({"ts": start + np.arange(n, dtype="int64") * step, "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": volume})


class FakeExchange:
    """Implements fetch_ohlcv / fetch_ticker(s) / fetch_order_book / load_markets / fetch_time."""

    def __init__(self, series: dict[tuple[str, str], pd.DataFrame] | None = None, now_ms: int | None = None) -> None:
        self.series = series or {}
        self.now_ms = now_ms
        self.calls: list[tuple[str, Any]] = []
        self._fail: list[Exception] = []
        self.markets: dict[str, Any] = {}
        self.forming: dict[tuple[str, str], list[float]] = {}

    def fail_next(self, *errors: Exception) -> None:
        self._fail.extend(errors)

    def _maybe_fail(self) -> None:
        if self._fail:
            raise self._fail.pop(0)

    def load_markets(self, reload: bool = False) -> dict[str, Any]:
        self.calls.append(("load_markets", reload))
        self._maybe_fail()
        self.markets = {
            "BTC/USDT": {"symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "spot": True, "active": True,
                         "precision": {"amount": 0.00001, "price": 0.01},
                         "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5.0}}},
            "ETH/USDT": {"symbol": "ETH/USDT", "base": "ETH", "quote": "USDT", "spot": True, "active": True,
                         "precision": {"amount": 0.0001, "price": 0.01},
                         "limits": {"amount": {"min": 0.0001}, "cost": {"min": 5.0}}},
        }
        return self.markets

    def fetch_time(self) -> int:
        self._maybe_fail()
        return self.now_ms or 0

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None) -> list:
        self.calls.append(("fetch_ohlcv", (symbol, timeframe, since, limit)))
        self._maybe_fail()
        if symbol not in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
            raise ccxt.BadSymbol(f"binance does not have market symbol {symbol}")
        df = self.series.get((symbol, timeframe))
        if df is None:
            return []
        limit = limit or 500
        if since is not None:
            df = df[df["ts"] >= since]
        else:
            df = df.tail(limit)
        rows = df.head(limit)[["ts", "open", "high", "low", "close", "volume"]].values.tolist()
        rows = [[int(r[0]), *r[1:]] for r in rows]
        forming = self.forming.get((symbol, timeframe))
        if forming and len(rows) < limit and (not rows or forming[0] > rows[-1][0]) and (since is None or forming[0] >= since):
            rows.append(list(forming))
        return rows

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        self._maybe_fail()
        df = self.series.get((symbol, "1m"))
        if df is None:
            df = self.series.get((symbol, "15m"))
        last = float(df["close"].iloc[-1]) if df is not None and len(df) else 100.0
        return {"symbol": symbol, "last": last, "bid": last * 0.9999, "ask": last * 1.0001, "open": last * 0.98,
                "high": last * 1.02, "low": last * 0.97, "change": last * 0.02, "percentage": 2.04,
                "baseVolume": 1000.0, "quoteVolume": 1000.0 * last, "timestamp": self.now_ms}

    def fetch_tickers(self, symbols: list[str]) -> dict[str, Any]:
        return {s: self.fetch_ticker(s) for s in symbols}

    def fetch_order_book(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        self._maybe_fail()
        mid = self.fetch_ticker(symbol)["last"]
        bids = [[mid * (1 - 0.0001 * (i + 1)), 1.0 + i] for i in range(limit)]
        asks = [[mid * (1 + 0.0001 * (i + 1)), 1.0 + i] for i in range(limit)]
        return {"bids": bids, "asks": asks, "timestamp": self.now_ms}


class FakeWebSocket:
    """An async context manager yielding canned messages, then closing (or raising)."""

    def __init__(self, messages: list[str], error: Exception | None = None) -> None:
        self.messages = messages
        self.error = error

    async def __aenter__(self) -> "FakeWebSocket":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self) -> "FakeWebSocket":
        return self

    async def __anext__(self) -> str:
        import asyncio

        await asyncio.sleep(0)
        if self.messages:
            return self.messages.pop(0)
        if self.error:
            raise self.error
        raise StopAsyncIteration
