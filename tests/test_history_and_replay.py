import pytest

from bot.data.history import download_ohlcv, load_csv
from bot.execution.replay import CsvMarketData
from tests.conftest import TF_MS, make_ohlcv
from tests.test_feed import FakeMarket


def test_load_csv_formats(tmp_path):
    canon = tmp_path / "a.csv"
    canon.write_text("ts,open,high,low,close,volume\n1700000000000,1,2,0.5,1.5,10\n1700003600000,1.5,2,1,1.2,5\n")
    df = load_csv(canon)
    assert df["ts"].tolist() == [1700000000000, 1700003600000] and df["close"].tolist() == [1.5, 1.2]

    seconds = tmp_path / "b.csv"
    seconds.write_text("time,open,high,low,close,vol\n1700000000,1,2,0.5,1.5,10\n")
    assert load_csv(seconds)["ts"].tolist() == [1700000000000]

    cdd = tmp_path / "c.csv"
    cdd.write_text("Timestamps are UTC timezone,https://www.CryptoDataDownload.com\n"
                   "date,symbol,open,high,low,close,Volume BTC,Volume USD\n"
                   "2019-10-17 09-AM,BTCUSD,8051,8056.83,8021.23,8035.88,61.25,492394.56\n"
                   "2019-10-17 08-AM,BTCUSD,7975.89,8070,7975.89,8051,370.45,2971610.86\n")
    df = load_csv(cdd)
    assert df["ts"].tolist() == [1571299200000, 1571302800000]  # sorted oldest first
    assert df["volume"].tolist() == [370.45, 61.25]

    iso = tmp_path / "d.csv"
    iso.write_text("datetime,open,high,low,close,volume\n2024-01-01T00:00:00Z,1,1,1,1,1\n")
    assert load_csv(iso)["ts"].tolist() == [1704067200000]


def test_sample_files_are_clean():
    for name in ("binance_BTCUSDT_1h_2020-11_2021-05.csv", "coinbase_BTCUSD_1h_2017-07_2019-10.csv"):
        df = load_csv(f"data/samples/{name}")
        assert len(df) > 4000
        assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-6).all()
        assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-6).all()
        assert df["ts"].is_monotonic_increasing and df["ts"].is_unique


def test_download_paginates_and_caches(tmp_path):
    df = make_ohlcv(range(100, 2600))
    market = FakeMarket(df)
    cache = tmp_path / "cache.csv"
    since, until = int(df["ts"].iloc[10]), int(df["ts"].iloc[2100])
    out = download_ohlcv(market, symbol="BTC/USDT", timeframe="1h", since_ms=since, until_ms=until, page_limit=1000, cache=cache)
    assert len(out) == 2091 and market.calls == 3
    assert cache.exists()
    market2 = FakeMarket(df)
    again = download_ohlcv(market2, symbol="BTC/USDT", timeframe="1h", since_ms=since, until_ms=until, page_limit=1000, cache=cache)
    assert len(again) == 2091 and market2.calls == 0  # fully served from cache


def test_replay_clock_and_candles():
    df = make_ohlcv(range(100, 130))
    replay = CsvMarketData(df, "1h", start_index=5)
    ts = df["ts"].tolist()
    assert replay.now_ms() == ts[5] + TF_MS + replay.clock_offset_ms
    rows = replay.fetch_ohlcv("BTC/USDT", "1h")
    assert rows[-1][0] == ts[6]  # includes the currently open candle, like a real exchange
    assert replay.fetch_ticker_price("BTC/USDT") == 105.0
    replay.advance()
    assert replay.fetch_ticker_price("BTC/USDT") == 106.0
    with pytest.raises(ValueError):
        replay.fetch_ohlcv("BTC/USDT", "4h")
    replay.advance(1000)
    assert replay.exhausted
