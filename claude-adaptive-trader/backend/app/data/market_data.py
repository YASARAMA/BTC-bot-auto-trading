"""Market data service: history, freshness and live prices behind one interface.

* History comes from the local cache, which is filled from Binance's REST API (paged,
  resumable) or from imported files.
* Live prices and forming candles come from the WebSocket feed, or from REST polling when
  the feed is down.
* Freshness is computed, never assumed: every response says how old the newest closed
  candle is, where it came from, and whether it is stale.

A forming candle is shown on charts but is never stored as complete and never reaches a
strategy. Decisions are taken on closed candles only.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from app.config import Settings
from app.core.clock import DAY_MS, now_ms
from app.core.errors import DataUnavailable, ExchangeUnavailable, TraderError
from app.core.events import EventBus
from app.core.jobs import JobContext
from app.core.timeframes import floor_ts, last_closed_open, ordered, tf_ms, validate_timeframe
from app.data.binance_client import BinancePublicClient, MarketRules, assumed_rules
from app.data.historical_store import CandleStore
from app.data.resample import resample
from app.data.validation import frame_from_rows, validate_candles
from app.monitoring.logging import get_logger, log_event

log = get_logger("market")

EXCHANGE = "binance"


def ws_symbol(pair: str) -> str:
    return pair.replace("/", "").lower()


@dataclass
class CandleSet:
    exchange: str
    pair: str
    timeframe: str
    candles: pd.DataFrame
    forming: dict[str, Any] | None
    sources: dict[str, int]
    derived_from: str | None
    freshness: dict[str, Any]
    notes: list[str] = field(default_factory=list)


class MarketDataService:
    def __init__(self, settings: Callable[[], Settings], store: CandleStore, client: BinancePublicClient | None,
                 bus: EventBus | None = None, clock: Callable[[], int] = now_ms) -> None:
        self._settings = settings
        self.store = store
        self.client = client
        self.bus = bus
        self.clock = clock
        self._lock = threading.RLock()
        self._series_locks: dict[tuple[str, str], threading.Lock] = {}
        self._tickers: dict[str, dict[str, Any]] = {}
        self._forming: dict[tuple[str, str], dict[str, Any]] = {}
        self._rules: dict[str, MarketRules] = {}
        self.ws: dict[str, Any] = {"enabled": True, "connected": False, "since_ms": None, "last_message_ms": None,
                                   "last_error": None, "reconnects": 0, "streams": 0, "messages": 0}

    @property
    def settings(self) -> Settings:
        return self._settings()

    def _series_lock(self, pair: str, timeframe: str) -> threading.Lock:
        with self._lock:
            return self._series_locks.setdefault((pair, timeframe), threading.Lock())

    # ----- history from Binance -----------------------------------------------------------
    def sync(self, pair: str, timeframe: str, *, days: int | None = None, start_ms: int | None = None,
             end_ms: int | None = None, ctx: JobContext | None = None) -> dict[str, Any]:
        """Bring the cached series up to date with Binance. Resumable and idempotent."""
        validate_timeframe(timeframe)
        if self.client is None:
            raise ExchangeUnavailable("no Binance client configured")
        step = tf_ms(timeframe)
        now = self.clock()
        newest_closed = last_closed_open(now, timeframe)
        if start_ms is None:
            span_days = days if days is not None else self.settings.exchange.history_days
            desired = floor_ts(now - span_days * DAY_MS, timeframe)
        else:
            desired = floor_ts(start_ms, timeframe)
        target_end = min(floor_ts(end_ms, timeframe) if end_ms else newest_closed, newest_closed)
        with self._series_lock(pair, timeframe):
            first = self.store.first_ts(EXCHANGE, pair, timeframe)
            latest = self.store.latest_ts(EXCHANGE, pair, timeframe)
            ranges: list[tuple[int, int]] = []
            if latest is None or first is None:
                ranges.append((desired, target_end))
            else:
                if desired < first:
                    ranges.append((desired, first - step))
                if latest < target_end:
                    ranges.append((max(latest + step, desired), target_end))
            total_bars = sum(max(0, (b - a) // step + 1) for a, b in ranges)
            fetched = written = pages = 0
            issues: list[str] = []
            try:
                for a, b in ranges:
                    cursor = a
                    while cursor <= b:
                        if ctx:
                            ctx.check()
                        rows = self.client.fetch_ohlcv(pair, timeframe, since=cursor, limit=1000)
                        pages += 1
                        if not rows:
                            break
                        df, report = validate_candles(frame_from_rows(rows), timeframe, now_ms=self.clock())
                        issues.extend(i for i in report.issues() if "still forming" not in i and "missing" not in i)
                        df = df[(df["ts"] >= a) & (df["ts"] <= b)]
                        written += self.store.upsert(EXCHANGE, pair, timeframe, df, "binance-rest")
                        last_row = int(max(r[0] for r in rows))
                        fetched += len(df)
                        if last_row < cursor:
                            break
                        cursor = last_row + step
                        if ctx and total_bars:
                            ctx.progress(min(0.99, fetched / total_bars), f"{pair} {timeframe}: {fetched}/{total_bars}")
                        if len(rows) < 1000 and cursor > newest_closed:
                            break
            except TraderError as exc:
                self.store.set_meta(EXCHANGE, pair, timeframe, status="error", error=exc.message)
                raise
            missing, gaps = self.store.gaps(EXCHANGE, pair, timeframe)
            result = {"exchange": EXCHANGE, "pair": pair, "timeframe": timeframe, "pages": pages,
                      "written": written, "requested_bars": total_bars, "missing_in_cache": missing,
                      "gaps": len(gaps), "issues": sorted(set(issues)),
                      "first_ts": self.store.first_ts(EXCHANGE, pair, timeframe),
                      "last_ts": self.store.latest_ts(EXCHANGE, pair, timeframe)}
            self.store.set_meta(EXCHANGE, pair, timeframe, status="ok", report=result)
        return result

    def sync_many(self, pairs: list[str], timeframes: list[str], *, days: int | None = None,
                  ctx: JobContext | None = None) -> list[dict[str, Any]]:
        out = []
        combos = [(p, t) for p in pairs for t in ordered(timeframes)]
        for i, (pair, tf) in enumerate(combos):
            if ctx:
                ctx.progress(i / max(1, len(combos)), f"downloading {pair} {tf}")
            try:
                out.append(self.sync(pair, tf, days=days))
            except TraderError as exc:
                out.append({"pair": pair, "timeframe": tf, "error": exc.message})
        return out

    def refresh_recent(self, pair: str, timeframe: str, bars: int = 300) -> dict[str, Any] | None:
        """Top up the most recent candles; quiet when the exchange is unreachable."""
        if self.client is None:
            return None
        now = self.clock()
        try:
            return self.sync(pair, timeframe, start_ms=now - bars * tf_ms(timeframe))
        except TraderError:
            return None

    # ----- exchange rules --------------------------------------------------------------------
    def rules(self, pair: str) -> MarketRules:
        with self._lock:
            cached = self._rules.get(pair)
        if cached:
            return cached
        rules: MarketRules | None = None
        if self.client is not None:
            try:
                rules = self.client.market_rules(pair)
                self.store.save_rules(EXCHANGE, pair, rules.to_dict())
            except TraderError:
                rules = None
        if rules is None:
            stored = self.store.load_rules(EXCHANGE, pair)
            if stored:
                rules = MarketRules(pair=pair, base=stored["base"], quote=stored["quote"], active=stored["active"],
                                    amount_step=stored["amount_step"], price_tick=stored["price_tick"],
                                    min_qty=stored["min_qty"], min_notional=stored["min_notional"],
                                    source=f"{stored['source']} (cached)")
            else:
                rules = assumed_rules(pair)
        with self._lock:
            if "assumed" not in rules.source:
                self._rules[pair] = rules
        return rules

    # ----- reading candles -------------------------------------------------------------------
    def candles(self, pair: str, timeframe: str, *, limit: int = 500, end: int | None = None,
                exchange: str = EXCHANGE, refresh: bool = False) -> CandleSet:
        validate_timeframe(timeframe)
        limit = max(10, min(int(limit), 5000))
        notes: list[str] = []
        if refresh and exchange == EXCHANGE and end is None and self.client is not None:
            fresh = self.freshness(pair, timeframe, exchange=exchange)
            if fresh["status"] != "fresh":
                if self.client.recently_failed() or self.refresh_recent(pair, timeframe, bars=limit) is None:
                    notes.append(f"Binance could not be reached; showing cached data. "
                                 f"{self.client.status.last_error or ''}".strip())
        df = self.store.load(exchange, pair, timeframe, end=end, limit=limit, newest=True, with_source=True)
        derived_from = None
        if df.empty:
            df, derived_from = self._derive(exchange, pair, timeframe, limit=limit, end=end)
            if derived_from:
                notes.append(f"no stored {timeframe} candles; built from complete {derived_from} candles")
        if df.empty:
            raise DataUnavailable(
                f"no {exchange} {pair} {timeframe} candles are stored locally"
                + ("" if exchange != EXCHANGE else "; download them from the Data panel or start the live feed"),
                details={"exchange": exchange, "pair": pair, "timeframe": timeframe})
        sources = df["source"].value_counts().to_dict() if "source" in df else {}
        forming = None
        if exchange == EXCHANGE and end is None:
            with self._lock:
                f = self._forming.get((pair, timeframe))
            if f and f["ts"] > int(df["ts"].iloc[-1]):
                forming = dict(f)
        return CandleSet(exchange=exchange, pair=pair, timeframe=timeframe, candles=df, forming=forming,
                         sources={str(k): int(v) for k, v in sources.items()}, derived_from=derived_from,
                         freshness=self.freshness(pair, timeframe, exchange=exchange,
                                                  latest=int(df["ts"].iloc[-1]),
                                                  latest_source=str(df["source"].iloc[-1]) if "source" in df else None),
                         notes=notes)

    def _derive(self, exchange: str, pair: str, timeframe: str, *, limit: int,
                end: int | None) -> tuple[pd.DataFrame, str | None]:
        target = tf_ms(timeframe)
        candidates = [s for s in self.store.series() if s["exchange"] == exchange and s["pair"] == pair]
        for s in sorted(candidates, key=lambda s: -tf_ms(s["timeframe"])):
            base_ms = tf_ms(s["timeframe"])
            if base_ms >= target or target % base_ms:
                continue
            per = target // base_ms
            base = self.store.load(exchange, pair, s["timeframe"], end=end, limit=(limit + 1) * per, newest=True,
                                   with_source=True)
            out = resample(base, s["timeframe"], timeframe)
            if out.empty:
                continue
            label = ",".join(sorted(base["source"].unique()))
            out["source"] = f"derived:{s['timeframe']} ({label})"
            return out.tail(limit).reset_index(drop=True), s["timeframe"]
        return pd.DataFrame(), None

    def history(self, pair: str, timeframe: str, *, start: int | None = None, end: int | None = None,
                exchange: str = EXCHANGE) -> pd.DataFrame:
        """Closed candles for research, oldest first. Builds from a lower timeframe if needed."""
        df = self.store.load(exchange, pair, timeframe, start=start, end=end, with_source=True)
        if not df.empty:
            return df
        target = tf_ms(timeframe)
        for s in sorted((s for s in self.store.series() if s["exchange"] == exchange and s["pair"] == pair),
                        key=lambda s: -tf_ms(s["timeframe"])):
            base_ms = tf_ms(s["timeframe"])
            if base_ms < target and target % base_ms == 0:
                base = self.store.load(exchange, pair, s["timeframe"], start=start,
                                       end=None if end is None else end + target, with_source=True)
                out = resample(base, s["timeframe"], timeframe)
                if end is not None:
                    out = out[out["ts"] <= end]
                if not out.empty:
                    label = ",".join(sorted(base["source"].unique()))
                    out["source"] = f"derived:{s['timeframe']} ({label})"
                    return out.reset_index(drop=True)
        return df

    # ----- freshness -------------------------------------------------------------------------
    def freshness(self, pair: str, timeframe: str, *, exchange: str = EXCHANGE, now: int | None = None,
                  latest: int | None = None, latest_source: str | None = None) -> dict[str, Any]:
        now = self.clock() if now is None else now
        step = tf_ms(timeframe)
        if latest is None:
            latest = self.store.latest_ts(exchange, pair, timeframe)
        grace_s = self.settings.exchange.stale_after_seconds
        expected = last_closed_open(now, timeframe)
        if latest is None:
            return {"exchange": exchange, "pair": pair, "timeframe": timeframe, "status": "empty", "stale": True,
                    "last_open_ts": None, "last_close_ts": None, "age_s": None, "behind_bars": None,
                    "expected_last_open": expected, "checked_at": now, "source": None}
        age_s = (now - (latest + step)) / 1000
        stale = age_s > step / 1000 + grace_s
        return {
            "exchange": exchange, "pair": pair, "timeframe": timeframe,
            "status": "stale" if stale else "fresh", "stale": stale,
            "last_open_ts": latest, "last_close_ts": latest + step, "age_s": round(age_s, 1),
            "behind_bars": max(0, (expected - latest) // step), "expected_last_open": expected,
            "checked_at": now, "source": latest_source,
        }

    # ----- live prices -------------------------------------------------------------------------
    def ticker(self, pair: str) -> dict[str, Any] | None:
        with self._lock:
            t = self._tickers.get(pair)
        if not t:
            return None
        out = dict(t)
        age = (self.clock() - t["received_ms"]) / 1000
        out["age_s"] = round(age, 1)
        out["stale"] = age > self.settings.exchange.stale_after_seconds
        return out

    def tickers(self) -> dict[str, dict[str, Any] | None]:
        return {p: self.ticker(p) for p in self.settings.exchange.all_pairs}

    def refresh_tickers(self) -> bool:
        if self.client is None:
            return False
        try:
            fetched = self.client.fetch_tickers(self.settings.exchange.all_pairs)
        except TraderError:
            return False
        for t in fetched.values():
            self.on_ticker(t)
        return True

    def order_book(self, pair: str, depth: int = 20) -> dict[str, Any]:
        if self.client is None:
            raise ExchangeUnavailable("no Binance client configured")
        return self.client.fetch_order_book(pair, depth)

    def on_ticker(self, ticker: dict[str, Any]) -> None:
        with self._lock:
            self._tickers[ticker["pair"]] = ticker
        if self.bus:
            self.bus.publish({"type": "ticker", "pair": ticker["pair"], "data": ticker})

    def on_kline(self, pair: str, timeframe: str, candle: dict[str, Any], closed: bool, source: str) -> None:
        ts = int(candle["ts"])
        o, h, lo, c, v = (float(candle[k]) for k in ("open", "high", "low", "close", "volume"))
        if min(o, h, lo, c) <= 0 or h < max(o, c, lo) or lo > min(o, c, h) or v < 0:
            log_event(log, 30, "ignored an invalid candle from the live feed", pair=pair, timeframe=timeframe,
                      candle=candle)
            return
        row = {"ts": ts, "open": o, "high": h, "low": lo, "close": c, "volume": v}
        if closed:
            df, _ = validate_candles(pd.DataFrame([row]), timeframe, now_ms=self.clock() + 1000)
            if not df.empty:
                self.store.upsert(EXCHANGE, pair, timeframe, df, source)
            with self._lock:
                f = self._forming.get((pair, timeframe))
                if f and f["ts"] <= ts:
                    self._forming.pop((pair, timeframe), None)
        else:
            with self._lock:
                self._forming[(pair, timeframe)] = {**row, "source": source, "received_ms": self.clock()}
        if self.bus:
            self.bus.publish({"type": "kline", "pair": pair, "timeframe": timeframe, "closed": closed,
                              "candle": row, "source": source})

    def handle_ws_message(self, message: dict[str, Any], symbols: dict[str, str]) -> None:
        data = message.get("data", message)
        event = data.get("e")
        self.ws["last_message_ms"] = self.clock()
        self.ws["messages"] += 1
        if event == "kline":
            k = data.get("k") or {}
            pair = symbols.get(str(k.get("s") or data.get("s") or "").lower())
            if not pair:
                return
            try:
                candle = {"ts": int(k["t"]), "open": k["o"], "high": k["h"], "low": k["l"], "close": k["c"],
                          "volume": k["v"]}
            except (KeyError, TypeError, ValueError):
                return
            self.on_kline(pair, str(k.get("i")), candle, bool(k.get("x")), "binance-ws")
        elif event == "24hrTicker":
            pair = symbols.get(str(data.get("s", "")).lower())
            if not pair:
                return
            try:
                last = float(data["c"])
                bid, ask = float(data.get("b") or 0) or None, float(data.get("a") or 0) or None
            except (KeyError, TypeError, ValueError):
                return
            if last <= 0:
                return
            if bid and ask and bid > ask:
                bid = ask = None
            mid = (bid + ask) / 2 if bid and ask else None
            self.on_ticker({
                "pair": pair, "last": last, "bid": bid, "ask": ask,
                "spread_pct": round((ask - bid) / mid * 100, 5) if mid else None,
                "open_24h": _num(data.get("o")), "high_24h": _num(data.get("h")), "low_24h": _num(data.get("l")),
                "change_24h": _num(data.get("p")), "change_pct_24h": _num(data.get("P")),
                "base_volume_24h": _num(data.get("v")), "quote_volume_24h": _num(data.get("q")),
                "ts": int(data.get("E") or self.clock()), "received_ms": self.clock(), "source": "binance-ws",
            })

    # ----- status ----------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        s = self.settings
        series = []
        for pair in s.exchange.all_pairs:
            for tf in ordered([s.exchange.signal_timeframe, *s.exchange.confirm_timeframes]):
                series.append(self.freshness(pair, tf))
        rest = self.client.status.to_dict() if self.client else {"reachable": None, "last_error": "no client"}
        ws = dict(self.ws)
        ws_age = (self.clock() - ws["last_message_ms"]) / 1000 if ws.get("last_message_ms") else None
        ws["last_message_age_s"] = round(ws_age, 1) if ws_age is not None else None
        live_ok = bool(ws.get("connected")) or bool(rest.get("reachable"))
        signal_fresh = all(not f["stale"] for f in series if f["timeframe"] == s.exchange.signal_timeframe
                           and f["pair"] in s.exchange.pairs)
        if live_ok and signal_fresh:
            status = "ok"
        elif live_ok or any(not f["stale"] for f in series):
            status = "degraded"
        else:
            status = "error"
        return {"status": status, "exchange": EXCHANGE, "rest": rest, "websocket": ws, "series": series,
                "tickers": {p: (t or {}).get("age_s") for p, t in self.tickers().items()}}

    def set_ws_state(self, **values: Any) -> None:
        self.ws.update(values)


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
