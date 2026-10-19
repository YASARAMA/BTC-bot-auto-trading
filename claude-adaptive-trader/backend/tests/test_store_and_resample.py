from __future__ import annotations

import pandas as pd
import pytest

from app.data.historical_store import CandleStore
from app.data.resample import align_higher, resample
from app.storage.database import Database
from app.storage.schema import MARKET_MIGRATIONS
from tests.fakes import synthetic_candles

H1 = 3_600_000
M15 = 900_000


@pytest.fixture
def store(tmp_path) -> CandleStore:
    return CandleStore(Database(tmp_path / "m.sqlite", MARKET_MIGRATIONS))


def test_upsert_load_and_labels(store: CandleStore) -> None:
    df = synthetic_candles(300)
    assert store.upsert("binance", "BTC/USDT", "15m", df, "binance-rest") == 300
    store.upsert("binance", "BTC/USDT", "15m", df.tail(10), "binance-ws")
    assert store.count("binance", "BTC/USDT", "15m") == 300
    assert store.sources("binance", "BTC/USDT", "15m") == {"binance-rest": 290, "binance-ws": 10}
    newest = store.load("binance", "BTC/USDT", "15m", limit=5, newest=True)
    assert newest["ts"].tolist() == df["ts"].tail(5).tolist()
    window = store.load("binance", "BTC/USDT", "15m", start=int(df.ts[10]), end=int(df.ts[19]))
    assert len(window) == 10
    pd.testing.assert_series_equal(window["close"].reset_index(drop=True),
                                   df["close"].iloc[10:20].reset_index(drop=True), check_names=False)


def test_inventory_and_gaps(store: CandleStore) -> None:
    df = synthetic_candles(100).drop(index=[40, 41]).reset_index(drop=True)
    store.upsert("coinbase", "BTC/USD", "15m", df, "local-file:x.csv")
    inv = store.inventory()
    assert len(inv) == 1
    row = inv[0]
    assert row["exchange"] == "coinbase" and row["missing"] == 2 and row["gap_count"] == 1
    assert row["expected"] == 100 and row["sources"] == {"local-file:x.csv": 98}
    assert store.delete_series("coinbase", "BTC/USD", "15m") == 98
    assert store.inventory() == []


def test_resample_only_complete_blocks() -> None:
    df = synthetic_candles(16, "15m", start=1_700_000_000_000 - 1_700_000_000_000 % H1)
    full = resample(df, "15m", "1h")
    assert len(full) == 4
    first = df.iloc[:4]
    assert full.iloc[0]["open"] == first["open"].iloc[0] and full.iloc[0]["close"] == first["close"].iloc[-1]
    assert full.iloc[0]["high"] == first["high"].max() and full.iloc[0]["low"] == first["low"].min()
    assert full.iloc[0]["volume"] == pytest.approx(first["volume"].sum())
    partial = resample(df.drop(index=[5]), "15m", "1h")
    assert len(partial) == 3          # the hour with a missing quarter is not an hour


def test_resample_rejects_impossible() -> None:
    with pytest.raises(ValueError):
        resample(synthetic_candles(10), "15m", "15m")


def test_align_higher_has_no_lookahead() -> None:
    start = 1_700_000_000_000 - 1_700_000_000_000 % H1
    base = synthetic_candles(16, "15m", start=start)
    hourly = resample(base, "15m", "1h")
    hourly["marker"] = range(len(hourly))
    aligned = align_higher(base, "15m", hourly, "1h", ["marker"], prefix="h_")
    # The first three quarters of hour 0 cannot see hour 0; the fourth (closing with it) can.
    assert aligned["h_marker"].iloc[:3].isna().all()
    assert aligned["h_marker"].iloc[3] == 0
    assert aligned["h_marker"].iloc[4:7].tolist() == [0, 0, 0]
    assert aligned["h_marker"].iloc[7] == 1
    # Each base row only sees hourly blocks that closed at or before its own close.
    for _, row in aligned.iterrows():
        if pd.notna(row["h_marker"]):
            block_close = hourly.loc[int(row["h_marker"]), "ts"] + H1
            assert block_close <= row["ts"] + M15


def test_align_higher_does_not_carry_forward_forever() -> None:
    start = 1_700_000_000_000 - 1_700_000_000_000 % H1
    base = synthetic_candles(40, "15m", start=start)
    hourly = resample(base, "15m", "1h").iloc[:2].copy()      # only the first two hours exist
    hourly["v"] = 1.0
    aligned = align_higher(base, "15m", hourly, "1h", ["v"], prefix="h_", max_age_blocks=2)
    assert aligned["h_v"].iloc[-1] != aligned["h_v"].iloc[-1]  # NaN: too old to use
