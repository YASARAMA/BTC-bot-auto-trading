from __future__ import annotations

import pytest

from app.core.errors import DataValidationError, InvalidRequest
from app.data.historical_store import CandleStore
from app.data.importer import import_csv, import_repo_samples, read_candle_csv, samples_dir
from app.storage.database import Database
from app.storage.schema import MARKET_MIGRATIONS
from tests.fakes import synthetic_candles


@pytest.fixture
def store(tmp_path) -> CandleStore:
    return CandleStore(Database(tmp_path / "m.sqlite", MARKET_MIGRATIONS))


def test_header_csv(store: CandleStore) -> None:
    csv = synthetic_candles(30, "1h").to_csv(index=False)
    res = import_csv(store, csv, filename="mine.csv", exchange="binance", pair="BTC/USDT", timeframe="1h")
    assert res["written"] == 30 and res["source"] == "local-file:mine.csv"
    assert store.sources("binance", "BTC/USDT", "1h") == {"local-file:mine.csv": 30}


def test_binance_archive_layout_with_microseconds(store: CandleStore) -> None:
    df = synthetic_candles(5, "1m")
    lines = [f"{int(r.ts) * 1000},{r.open},{r.high},{r.low},{r.close},{r.volume},{int(r.ts) * 1000 + 59_999_999},"
             f"0,0,0,0,0" for r in df.itertuples()]
    parsed = read_candle_csv("\n".join(lines))
    assert parsed["ts"].tolist() == df["ts"].tolist()


def test_iso_dates_and_seconds(store: CandleStore) -> None:
    text = "date,open,high,low,close,volume\n2024-01-01T00:00:00Z,1,2,0.5,1.5,10\n2024-01-01T01:00:00Z,1.5,2,1,1.8,5\n"
    assert read_candle_csv(text)["ts"].tolist() == [1704067200000, 1704070800000]
    secs = "timestamp,open,high,low,close,volume\n1704067200,1,2,0.5,1.5,10\n"
    assert read_candle_csv(secs)["ts"].tolist() == [1704067200000]


def test_volume_is_required_never_invented() -> None:
    with pytest.raises(DataValidationError, match="volume"):
        read_candle_csv("ts,open,high,low,close\n0,1,2,0.5,1.5\n")


def test_unknown_exchange_rejected(store: CandleStore) -> None:
    csv = synthetic_candles(3, "1h").to_csv(index=False)
    with pytest.raises(InvalidRequest):
        import_csv(store, csv, filename="x.csv", exchange="madeup", pair="BTC/USDT", timeframe="1h")


def test_all_invalid_file_rejected(store: CandleStore) -> None:
    with pytest.raises(DataValidationError):
        import_csv(store, "ts,open,high,low,close,volume\n1,-1,-1,-1,-1,1\n", filename="x.csv", exchange="binance",
                   pair="BTC/USDT", timeframe="1h")


@pytest.mark.skipif(not samples_dir().exists(), reason="repository samples not present")
def test_repository_samples_keep_their_true_exchange(store: CandleStore) -> None:
    results = import_repo_samples(store)
    by_exchange = {r["exchange"]: r for r in results}
    assert by_exchange["binance"]["pair"] == "BTC/USDT" and by_exchange["binance"]["written"] > 4000
    assert by_exchange["coinbase"]["pair"] == "BTC/USD" and by_exchange["coinbase"]["written"] > 20000
    assert store.count("binance", "BTC/USD", "1h") == 0          # Coinbase data never filed as Binance
    assert store.sources("coinbase", "BTC/USD", "1h") == {"local-file:coinbase_BTCUSD_1h_2017-07_2019-10.csv": 20111}
