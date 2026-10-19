"""Binance public market data through CCXT.

This client never holds an API key: it is constructed without credentials, it refuses to
start if any appear on the exchange object, and it only calls public endpoints (candles,
tickers, order book, exchange rules, server time). There is no order method here, and no
other module in the project has one either.

Failures are expected - networks drop, Binance rate-limits, some networks block it
entirely - so every call goes through ``_call``: bounded retries with exponential backoff
for transient errors, none for programming errors, and a cool-down after repeated failures
so an unreachable exchange is not hammered. The ``status`` dictionary records the last
success, the last error and the current cool-down for the health page.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import ccxt

from app.core.clock import now_ms
from app.core.errors import DataValidationError, ExchangeUnavailable, InvalidRequest
from app.core.timeframes import tf_ms
from app.monitoring.logging import get_logger, log_event

log = get_logger("binance")

TRANSIENT = (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout, ccxt.DDoSProtection,
             ccxt.RateLimitExceeded)
PERMANENT = (ccxt.BadSymbol, ccxt.BadRequest, ccxt.AuthenticationError, ccxt.PermissionDenied,
             ccxt.NotSupported)


@dataclass
class ClientStatus:
    reachable: bool | None = None
    last_success_ms: int | None = None
    last_error: str | None = None
    last_error_ms: int | None = None
    consecutive_failures: int = 0
    cooldown_until_ms: int = 0
    latency_ms: float | None = None
    calls: int = 0
    failures: int = 0
    by_endpoint: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        now = now_ms()
        return {
            "reachable": self.reachable,
            "last_success_ms": self.last_success_ms,
            "last_error": self.last_error,
            "last_error_ms": self.last_error_ms,
            "consecutive_failures": self.consecutive_failures,
            "cooldown_s": max(0.0, round((self.cooldown_until_ms - now) / 1000, 1)),
            "latency_ms": self.latency_ms,
            "calls": self.calls,
            "failures": self.failures,
            "by_endpoint": self.by_endpoint,
        }


@dataclass(frozen=True)
class MarketRules:
    pair: str
    base: str
    quote: str
    active: bool
    amount_step: float | None
    price_tick: float | None
    min_qty: float | None
    min_notional: float | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {"pair": self.pair, "base": self.base, "quote": self.quote, "active": self.active,
                "amount_step": self.amount_step, "price_tick": self.price_tick, "min_qty": self.min_qty,
                "min_notional": self.min_notional, "source": self.source}


# Used only when the exchange rules cannot be fetched (offline). Labelled as assumptions.
ASSUMED_RULES: dict[str, dict[str, float]] = {
    "BTC": {"amount_step": 0.00001, "price_tick": 0.01, "min_qty": 0.00001, "min_notional": 5.0},
    "ETH": {"amount_step": 0.0001, "price_tick": 0.01, "min_qty": 0.0001, "min_notional": 5.0},
    "SOL": {"amount_step": 0.001, "price_tick": 0.01, "min_qty": 0.001, "min_notional": 5.0},
}
_DEFAULT_ASSUMED = {"amount_step": 0.001, "price_tick": 0.0001, "min_qty": 0.001, "min_notional": 5.0}


def assumed_rules(pair: str) -> MarketRules:
    base, quote = pair.split("/")
    r = ASSUMED_RULES.get(base, _DEFAULT_ASSUMED)
    return MarketRules(pair=pair, base=base, quote=quote, active=True, amount_step=r["amount_step"],
                       price_tick=r["price_tick"], min_qty=r["min_qty"], min_notional=r["min_notional"],
                       source="assumed (exchange rules not available)")


def make_exchange(rate_limit_ms: int, timeout_ms: int) -> Any:
    return ccxt.binance({
        "enableRateLimit": True,
        "rateLimit": rate_limit_ms,
        "timeout": timeout_ms,
        "options": {
            "defaultType": "spot",
            "fetchMarkets": ["spot"],      # spot rules only; futures/margin endpoints are never called
            "fetchMargins": False,
            "fetchCurrencies": False,      # a private endpoint on Binance; we hold no key
            "adjustForTimeDifference": False,
        },
    })


class BinancePublicClient:
    exchange_id = "binance"

    def __init__(self, *, rate_limit_ms: int = 1200, timeout_ms: int = 15000, exchange: Any | None = None,
                 max_retries: int = 2, backoff_base_s: float = 0.5, sleep: Callable[[float], None] = time.sleep) -> None:
        self._ex = exchange if exchange is not None else make_exchange(rate_limit_ms, timeout_ms)
        for attr in ("apiKey", "secret", "password", "uid", "privateKey"):
            if getattr(self._ex, attr, None):
                raise RuntimeError("the public market-data client must not hold exchange credentials")
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep
        self._lock = threading.Lock()
        self.status = ClientStatus()
        self._markets_loaded_ms = 0

    # ----- plumbing ------------------------------------------------------------------------
    def _cooldown_s(self) -> float:
        return min(300.0, 5.0 * (2 ** max(0, self.status.consecutive_failures - 3)))

    def _call(self, endpoint: str, fn: Callable[[], Any]) -> Any:
        now = now_ms()
        if self.status.cooldown_until_ms > now:
            wait = (self.status.cooldown_until_ms - now) / 1000
            raise ExchangeUnavailable(
                f"Binance is unreachable ({self.status.last_error}); next attempt in {wait:.0f}s",
                details={"endpoint": endpoint})
        attempt = 0
        while True:
            started = time.perf_counter()
            try:
                result = fn()
            except PERMANENT as exc:
                self._record(endpoint, ok=False, error=f"{type(exc).__name__}: {exc}", transient=False)
                raise InvalidRequest(f"Binance rejected the request: {exc}", details={"endpoint": endpoint}) from None
            except TRANSIENT as exc:
                attempt += 1
                if attempt > self.max_retries:
                    self._record(endpoint, ok=False, error=f"{type(exc).__name__}: {_short(exc)}", transient=True)
                    raise ExchangeUnavailable(
                        f"Binance is unreachable: {_short(exc)}", details={"endpoint": endpoint}) from None
                self._sleep(self.backoff_base_s * (2 ** (attempt - 1)))
                continue
            except ccxt.BaseError as exc:
                self._record(endpoint, ok=False, error=f"{type(exc).__name__}: {_short(exc)}", transient=True)
                raise ExchangeUnavailable(f"Binance error: {_short(exc)}", details={"endpoint": endpoint}) from None
            latency = round((time.perf_counter() - started) * 1000, 1)
            self._record(endpoint, ok=True, latency=latency)
            return result

    def _record(self, endpoint: str, *, ok: bool, latency: float | None = None, error: str | None = None,
                transient: bool = True) -> None:
        with self._lock:
            st = self.status
            st.calls += 1
            ep = st.by_endpoint.setdefault(endpoint, {"calls": 0, "failures": 0, "last_ok_ms": None,
                                                      "last_error": None})
            ep["calls"] += 1
            was_reachable = st.reachable
            if ok:
                st.reachable = True
                st.last_success_ms = now_ms()
                st.latency_ms = latency
                st.consecutive_failures = 0
                st.cooldown_until_ms = 0
                ep["last_ok_ms"] = st.last_success_ms
                if was_reachable is False:
                    log_event(log, 20, "Binance public API reachable again", endpoint=endpoint)
                return
            st.failures += 1
            ep["failures"] += 1
            ep["last_error"] = error
            st.last_error, st.last_error_ms = error, now_ms()
            if transient:
                st.consecutive_failures += 1
                st.reachable = False
                if st.consecutive_failures >= 3:
                    st.cooldown_until_ms = now_ms() + int(self._cooldown_s() * 1000)
            if was_reachable is not False and transient:
                log_event(log, 30, "Binance public API unreachable", endpoint=endpoint, error=error)

    def recently_failed(self, window_ms: int = 30_000) -> bool:
        """True while cooling down, or when the last call failed within ``window_ms``.

        Interactive requests use this to answer from the cache at once instead of waiting
        for a network timeout that the background poller has just experienced.
        """
        st = self.status
        if st.cooldown_until_ms > now_ms():
            return True
        return st.reachable is False and st.last_error_ms is not None and now_ms() - st.last_error_ms < window_ms

    # ----- public endpoints ------------------------------------------------------------------
    def ping(self) -> float:
        self._call("time", lambda: self._ex.fetch_time())
        return float(self.status.latency_ms or 0.0)

    def load_markets(self, reload: bool = False) -> dict[str, Any]:
        if reload or not self._markets_loaded_ms or now_ms() - self._markets_loaded_ms > 3_600_000:
            markets = self._call("markets", lambda: self._ex.load_markets(reload=True))
            self._markets_loaded_ms = now_ms()
            return markets
        return self._ex.markets

    def market_rules(self, pair: str) -> MarketRules:
        markets = self.load_markets()
        m = markets.get(pair)
        if not m:
            raise InvalidRequest(f"{pair} is not a Binance spot market")
        if not m.get("spot", True):
            raise InvalidRequest(f"{pair} is not a spot market")
        limits = m.get("limits") or {}
        precision = m.get("precision") or {}
        return MarketRules(
            pair=pair, base=m.get("base", pair.split("/")[0]), quote=m.get("quote", pair.split("/")[1]),
            active=bool(m.get("active", True)),
            amount_step=_f(precision.get("amount")), price_tick=_f(precision.get("price")),
            min_qty=_f((limits.get("amount") or {}).get("min")),
            min_notional=_f((limits.get("cost") or {}).get("min")),
            source="binance exchangeInfo")

    def fetch_ohlcv(self, pair: str, timeframe: str, since: int | None = None, limit: int = 1000) -> list[list[Any]]:
        tf_ms(timeframe)
        limit = max(1, min(int(limit), 1000))
        rows = self._call("klines", lambda: self._ex.fetch_ohlcv(pair, timeframe, since=since, limit=limit))
        if not isinstance(rows, list) or any(not isinstance(r, (list, tuple)) or len(r) < 6 for r in rows):
            raise DataValidationError("Binance returned candles in an unexpected shape")
        return [list(r[:6]) for r in rows]

    def fetch_ticker(self, pair: str) -> dict[str, Any]:
        t = self._call("ticker", lambda: self._ex.fetch_ticker(pair))
        return normalize_ticker(pair, t, source="binance-rest")

    def fetch_tickers(self, pairs: list[str]) -> dict[str, dict[str, Any]]:
        raw = self._call("tickers", lambda: self._ex.fetch_tickers(pairs))
        return {p: normalize_ticker(p, raw[p], source="binance-rest") for p in pairs if p in raw}

    def fetch_order_book(self, pair: str, depth: int = 20) -> dict[str, Any]:
        depth = max(5, min(int(depth), 100))
        ob = self._call("orderbook", lambda: self._ex.fetch_order_book(pair, limit=depth))
        bids = [[float(p), float(q)] for p, q, *_ in (ob.get("bids") or [])][:depth]
        asks = [[float(p), float(q)] for p, q, *_ in (ob.get("asks") or [])][:depth]
        if bids and asks and bids[0][0] >= asks[0][0]:
            raise DataValidationError("crossed order book from Binance (best bid >= best ask)")
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
        return {
            "pair": pair, "bids": bids, "asks": asks, "best_bid": best_bid, "best_ask": best_ask,
            "spread_pct": round((best_ask - best_bid) / mid * 100, 5) if mid else None,
            "ts": ob.get("timestamp") or now_ms(), "received_ms": now_ms(), "source": "binance-rest",
        }


def normalize_ticker(pair: str, t: dict[str, Any], *, source: str) -> dict[str, Any]:
    last = _f(t.get("last") or t.get("close"))
    if last is None or last <= 0:
        raise DataValidationError(f"Binance returned no valid last price for {pair}")
    bid, ask = _f(t.get("bid")), _f(t.get("ask"))
    if bid is not None and ask is not None and bid > ask:
        bid = ask = None  # a crossed quote is not a usable quote
    mid = (bid + ask) / 2 if bid and ask else None
    return {
        "pair": pair,
        "last": last,
        "bid": bid,
        "ask": ask,
        "spread_pct": round((ask - bid) / mid * 100, 5) if mid else None,
        "open_24h": _f(t.get("open")),
        "high_24h": _f(t.get("high")),
        "low_24h": _f(t.get("low")),
        "change_24h": _f(t.get("change")),
        "change_pct_24h": _f(t.get("percentage")),
        "base_volume_24h": _f(t.get("baseVolume")),
        "quote_volume_24h": _f(t.get("quoteVolume")),
        "ts": int(t.get("timestamp") or now_ms()),
        "received_ms": now_ms(),
        "source": source,
    }


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        v = float(value)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _short(exc: BaseException, limit: int = 200) -> str:
    text = str(exc).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."
