import pandas as pd
import pytest

from bot.data.feed import CandleFeed
from bot.execution.base import MarketData, MarketInfo
from tests.conftest import TF_MS, make_ohlcv


class FakeMarket(MarketData):
    def __init__(self, df: pd.DataFrame, drop_ts=(), fail_times: int = 0):
        self.df = df
        self.drop_ts = set(drop_ts)
        self.fail_times = fail_times
        self.calls = 0
        self.since_seen = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls += 1
        self.since_seen.append(since)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("exchange down")
        frame = self.df
        if since is not None:
            frame = frame[frame["ts"] >= since]
        # Only the first full fetch drops candles (simulating a truncated response).
        if self.calls == 1:
            frame = frame[~frame["ts"].isin(self.drop_ts)]
        if limit:
            frame = frame.iloc[:limit]
        return frame[["ts", "open", "high", "low", "close", "volume"]].values.tolist()

    def fetch_ticker_price(self, symbol):
        return float(self.df["close"].iloc[-1])

    def market_info(self, symbol):
        return MarketInfo()


def make_feed(market, window=50, **kw):
    return CandleFeed(market, symbol="BTC/USDT", timeframe="1h", window=window, sleep=lambda _s: None, **kw)


def test_only_closed_candles_are_returned():
    df = make_ohlcv(range(100, 200))
    market = FakeMarket(df)
    feed = make_feed(market)
    ts = df["ts"].tolist()
    now = ts[60] + 600_000  # ten minutes into candle 60 -> candle 59 is the last closed one
    new = feed.refresh(now)
    assert feed.latest_ts == ts[59]
    assert len(new) == 50 and len(feed.candles()) == 50  # window applied
    assert feed.refresh(now).empty  # nothing new
    new2 = feed.refresh(ts[61] + 1_000)
    assert new2["ts"].tolist() == [ts[60]]
    assert len(feed.candles()) == 50


def test_gap_is_backfilled():
    df = make_ohlcv(range(100, 200))
    ts = df["ts"].tolist()
    market = FakeMarket(df, drop_ts={ts[30], ts[31]})
    feed = make_feed(market, window=60)
    feed.refresh(ts[70] + 1)
    have = feed.candles()["ts"].tolist()
    assert ts[30] in have and ts[31] in have
    assert feed.gaps_detected == 1
    assert (pd.Series(have).diff().dropna() == TF_MS).all()


def test_retries_then_succeeds():
    df = make_ohlcv(range(100, 200))
    market = FakeMarket(df, fail_times=2)
    feed = make_feed(market, max_retries=3, backoff_base_seconds=0.01, backoff_max_seconds=0.01)
    feed.refresh(int(df["ts"].iloc[-1]) + TF_MS + 1)
    assert market.calls == 3 and feed.latest_ts == int(df["ts"].iloc[-1])


def test_gives_up_after_max_retries():
    df = make_ohlcv(range(100, 120))
    market = FakeMarket(df, fail_times=10)
    feed = make_feed(market, max_retries=2, backoff_base_seconds=0.01, backoff_max_seconds=0.01)
    with pytest.raises(ConnectionError):
        feed.refresh(int(df["ts"].iloc[-1]) + TF_MS + 1)
    assert market.calls == 3


def test_time_helpers():
    feed = make_feed(FakeMarket(make_ohlcv([1, 2, 3])))
    now = 1_700_000_000_000
    assert feed.next_close_ms(now) % TF_MS == 0 and feed.next_close_ms(now) > now
    assert feed.last_closed_ts(now) == feed.next_close_ms(now) - 2 * TF_MS
