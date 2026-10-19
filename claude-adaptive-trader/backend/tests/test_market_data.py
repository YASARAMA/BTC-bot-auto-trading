from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.core.errors import DataUnavailable
from app.data.binance_client import BinancePublicClient
from app.data.historical_store import CandleStore
from app.data.market_data import MarketDataService
from app.storage.database import Database
from app.storage.schema import MARKET_MIGRATIONS
from tests.fakes import FakeExchange, synthetic_candles

M15 = 900_000


class Clock:
    def __init__(self, t: int) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


def build(tmp_path, n: int = 2500, settings: Settings | None = None):
    series = synthetic_candles(n, "15m")
    ex = FakeExchange({("BTC/USDT", "15m"): series})
    last_open = int(series["ts"].iloc[-1])
    clock = Clock(last_open + M15 + 1_000)          # every candle has closed
    store = CandleStore(Database(tmp_path / "m.sqlite", MARKET_MIGRATIONS))
    s = settings or Settings()
    svc = MarketDataService(lambda: s, store, BinancePublicClient(exchange=ex, sleep=lambda x: None), clock=clock)
    return svc, ex, series, clock


def test_sync_pages_and_resumes(tmp_path) -> None:
    svc, ex, series, clock = build(tmp_path)
    first = int(series["ts"].iloc[0])
    res = svc.sync("BTC/USDT", "15m", start_ms=first)
    assert res["written"] == 2500 and res["pages"] >= 3 and res["missing_in_cache"] == 0
    assert svc.store.sources("binance", "BTC/USDT", "15m") == {"binance-rest": 2500}
    calls = len(ex.calls)
    again = svc.sync("BTC/USDT", "15m", start_ms=first)
    assert again["written"] == 0 and len(ex.calls) == calls      # nothing new: no request at all


def test_forming_candle_is_never_stored(tmp_path) -> None:
    svc, ex, series, clock = build(tmp_path, n=50)
    last = int(series["ts"].iloc[-1])
    ex.forming[("BTC/USDT", "15m")] = [last + M15, 1.0, 2.0, 0.5, 1.5, 3.0]
    clock.t = last + M15 + 60_000                    # one minute into the next candle
    svc.sync("BTC/USDT", "15m", start_ms=int(series["ts"].iloc[0]))
    assert svc.store.latest_ts("binance", "BTC/USDT", "15m") == last


def test_freshness(tmp_path) -> None:
    svc, ex, series, clock = build(tmp_path, n=100)
    svc.sync("BTC/USDT", "15m", start_ms=int(series["ts"].iloc[0]))
    last = int(series["ts"].iloc[-1])
    clock.t = last + M15 + 10_000
    f = svc.freshness("BTC/USDT", "15m")
    assert f["status"] == "fresh" and f["behind_bars"] == 0
    clock.t = last + 2 * M15 + 60_000                # next candle closed a minute ago: within grace
    assert svc.freshness("BTC/USDT", "15m")["status"] == "fresh"
    clock.t = last + 2 * M15 + 181_000               # grace (180 s) exceeded
    stale = svc.freshness("BTC/USDT", "15m")
    assert stale["status"] == "stale" and stale["stale"] and stale["behind_bars"] == 1
    assert svc.freshness("ETH/USDT", "15m")["status"] == "empty"


def test_unreachable_exchange_serves_cache_and_says_so(tmp_path) -> None:
    import ccxt

    svc, ex, series, clock = build(tmp_path, n=100)
    svc.sync("BTC/USDT", "15m", start_ms=int(series["ts"].iloc[0]))
    clock.t += 10 * M15                               # time passes, the cache goes stale
    ex.fail_next(*[ccxt.NetworkError("offline")] * 10)
    cs = svc.candles("BTC/USDT", "15m", limit=50, refresh=True)
    assert len(cs.candles) == 50 and cs.freshness["stale"]
    assert any("could not be reached" in n for n in cs.notes)


def test_missing_data_is_an_error_not_a_fabrication(tmp_path) -> None:
    svc, *_ = build(tmp_path, n=10)
    with pytest.raises(DataUnavailable):
        svc.candles("ETH/USDT", "1h", refresh=False)


def test_derived_timeframe_is_labelled(tmp_path) -> None:
    svc, ex, series, clock = build(tmp_path, n=400)
    svc.sync("BTC/USDT", "15m", start_ms=int(series["ts"].iloc[0]))
    cs = svc.candles("BTC/USDT", "1h", limit=50, refresh=False)
    assert cs.derived_from == "15m"
    assert all(src.startswith("derived:15m") for src in cs.sources)
    assert len(cs.candles) == 50


def test_websocket_messages(tmp_path) -> None:
    svc, ex, series, clock = build(tmp_path, n=10)
    symbols = {"btcusdt": "BTC/USDT"}
    t0 = int(series["ts"].iloc[-1]) + M15
    kline = {"stream": "btcusdt@kline_15m", "data": {"e": "kline", "s": "BTCUSDT", "k": {
        "t": t0, "i": "15m", "o": "100", "h": "110", "l": "95", "c": "105", "v": "12", "x": False}}}
    svc.handle_ws_message(kline, symbols)
    assert svc.store.latest_ts("binance", "BTC/USDT", "15m") is None      # forming: not stored
    clock.t = t0 + M15 + 1
    kline["data"]["k"]["x"] = True
    svc.handle_ws_message(kline, symbols)
    assert svc.store.latest_ts("binance", "BTC/USDT", "15m") == t0
    assert svc.store.sources("binance", "BTC/USDT", "15m") == {"binance-ws": 1}
    bad = json.loads(json.dumps(kline))
    bad["data"]["k"].update({"t": t0 + M15, "h": "90"})                  # high below open: invalid
    svc.handle_ws_message(bad, symbols)
    assert svc.store.latest_ts("binance", "BTC/USDT", "15m") == t0
    ticker = {"data": {"e": "24hrTicker", "s": "BTCUSDT", "c": "105.5", "b": "105.4", "a": "105.6", "P": "1.2",
                       "v": "1000", "q": "105500", "E": clock.t}}
    svc.handle_ws_message(ticker, symbols)
    t = svc.ticker("BTC/USDT")
    assert t["last"] == 105.5 and t["source"] == "binance-ws" and not t["stale"]
    clock.t += 10 * 60_000
    assert svc.ticker("BTC/USDT")["stale"]
