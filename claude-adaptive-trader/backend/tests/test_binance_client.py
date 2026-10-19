from __future__ import annotations

import ccxt
import pytest

from app.core.errors import DataValidationError, ExchangeUnavailable, InvalidRequest
from app.data.binance_client import BinancePublicClient, normalize_ticker
from tests.fakes import FakeExchange, synthetic_candles


def client(ex: FakeExchange, **kw) -> BinancePublicClient:
    return BinancePublicClient(exchange=ex, sleep=lambda s: None, **kw)


def test_refuses_credentials() -> None:
    ex = FakeExchange()
    ex.apiKey = "abc"
    with pytest.raises(RuntimeError):
        BinancePublicClient(exchange=ex)


def test_transient_errors_are_retried() -> None:
    ex = FakeExchange({("BTC/USDT", "15m"): synthetic_candles(10)})
    ex.fail_next(ccxt.RequestTimeout("slow"), ccxt.NetworkError("reset"))
    c = client(ex, max_retries=2)
    rows = c.fetch_ohlcv("BTC/USDT", "15m", since=0, limit=5)
    assert len(rows) == 5 and c.status.reachable is True and c.status.consecutive_failures == 0


def test_gives_up_and_reports_unreachable() -> None:
    ex = FakeExchange()
    ex.fail_next(*[ccxt.NetworkError("down")] * 3)
    c = client(ex, max_retries=2)
    with pytest.raises(ExchangeUnavailable):
        c.fetch_ohlcv("BTC/USDT", "15m")
    assert c.status.reachable is False and "down" in c.status.last_error
    assert c.recently_failed()


def test_permanent_errors_not_retried() -> None:
    ex = FakeExchange()
    c = client(ex)
    with pytest.raises(InvalidRequest):
        c.fetch_ohlcv("NOPE/USDT", "15m")
    assert len([x for x in ex.calls if x[0] == "fetch_ohlcv"]) == 1


def test_cooldown_after_repeated_failures() -> None:
    ex = FakeExchange()
    c = client(ex, max_retries=0)
    for _ in range(3):
        ex.fail_next(ccxt.NetworkError("down"))
        with pytest.raises(ExchangeUnavailable):
            c.fetch_ticker("BTC/USDT")
    calls_before = len(ex.calls)
    with pytest.raises(ExchangeUnavailable, match="next attempt"):
        c.fetch_ticker("BTC/USDT")           # refused locally, the exchange is not called
    assert len(ex.calls) == calls_before
    assert c.status.to_dict()["cooldown_s"] > 0


def test_market_rules_from_exchange_info() -> None:
    r = client(FakeExchange()).market_rules("BTC/USDT")
    assert r.amount_step == 0.00001 and r.min_notional == 5.0 and r.source == "binance exchangeInfo"


def test_ticker_validation() -> None:
    t = normalize_ticker("BTC/USDT", {"last": 100, "bid": 99.9, "ask": 100.1, "percentage": 1.5}, source="x")
    assert t["spread_pct"] == pytest.approx(0.2, rel=1e-3)
    with pytest.raises(DataValidationError):
        normalize_ticker("BTC/USDT", {"last": None}, source="x")
    crossed = normalize_ticker("BTC/USDT", {"last": 100, "bid": 101, "ask": 99}, source="x")
    assert crossed["bid"] is None and crossed["spread_pct"] is None


def test_order_book_shape() -> None:
    ob = client(FakeExchange({("BTC/USDT", "15m"): synthetic_candles(5)})).fetch_order_book("BTC/USDT", 10)
    assert len(ob["bids"]) == 10 and ob["best_bid"] < ob["best_ask"] and ob["spread_pct"] > 0
