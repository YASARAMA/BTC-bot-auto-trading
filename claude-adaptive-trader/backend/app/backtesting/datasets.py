"""Datasets with a chronological development / validation / test split.

The split is fixed when a dataset is first registered: boundaries are stored together with
a fingerprint (hash) of the test segment's candles. From then on

* development and validation segments are open to research;
* the test segment can only be read through a *final evaluation*, which is logged with who
  asked, why and for which strategy versions;
* if the test candles ever change (a re-import, a correction, a different source), the
  fingerprint no longer matches and final evaluation refuses to run.

Warm-up for a later segment may use candles from earlier segments (they are in the past);
nothing may use candles after the segment's end.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.config import SplitConfig
from app.core.clock import now_ms
from app.core.errors import DataUnavailable, InvalidRequest, TestSetLocked
from app.core.hashing import stable_hash
from app.core.timeframes import tf_ms
from app.data.market_data import MarketDataService
from app.storage.database import Database, from_json, to_json

SEGMENTS = ("development", "validation", "test")


@dataclass
class Dataset:
    id: str
    exchange: str
    pairs: list[str]
    timeframe: str
    start_ts: int
    development_end_ts: int
    validation_end_ts: int
    end_ts: int
    test_fingerprint: str
    sources: dict[str, Any]

    def window(self, segment: str) -> tuple[int, int]:
        step = tf_ms(self.timeframe)
        if segment == "development":
            return self.start_ts, self.development_end_ts
        if segment == "validation":
            return self.development_end_ts + step, self.validation_end_ts
        if segment == "test":
            return self.validation_end_ts + step, self.end_ts
        if segment == "research":            # development + validation, never the test
            return self.start_ts, self.validation_end_ts
        raise InvalidRequest(f"unknown segment {segment!r}")

    def to_dict(self) -> dict[str, Any]:
        out = {k: getattr(self, k) for k in ("id", "exchange", "pairs", "timeframe", "start_ts", "development_end_ts",
                                              "validation_end_ts", "end_ts", "test_fingerprint", "sources")}
        out["segments"] = {s: dict(zip(("start", "end"), self.window(s))) for s in SEGMENTS}
        return out


def load_candles(market: MarketDataService, exchange: str, pairs: list[str], timeframe: str,
                 end: int | None = None) -> dict[str, pd.DataFrame]:
    out = {}
    for pair in pairs:
        df = market.history(pair, timeframe, end=end, exchange=exchange)
        if df.empty:
            raise DataUnavailable(f"no stored {exchange} {pair} {timeframe} candles; download or import them first")
        out[pair] = df.reset_index(drop=True)
    return out


def fingerprint(candles: dict[str, pd.DataFrame], start: int, end: int) -> str:
    h = hashlib.sha256()
    for pair in sorted(candles):
        df = candles[pair]
        part = df[(df["ts"] >= start) & (df["ts"] <= end)][["ts", "open", "high", "low", "close", "volume"]]
        h.update(pair.encode())
        h.update(pd.util.hash_pandas_object(part.round(10), index=False).values.tobytes())
    return h.hexdigest()[:32]


def register(db: Database, market: MarketDataService, *, exchange: str, pairs: list[str], timeframe: str,
             split: SplitConfig) -> Dataset:
    pairs = sorted(pairs)
    candles = load_candles(market, exchange, pairs, timeframe)
    start = max(int(df["ts"].iloc[0]) for df in candles.values())
    end = min(int(df["ts"].iloc[-1]) for df in candles.values())
    step = tf_ms(timeframe)
    bars = (end - start) // step + 1
    if bars < 600:
        raise InvalidRequest(f"only {bars} common candles; a frozen split needs at least 600")
    dev_end = start + int(bars * split.development) * step - step
    val_end = start + int(bars * (split.development + split.validation)) * step - step
    ds_id = "ds-" + stable_hash({"exchange": exchange, "pairs": pairs, "tf": timeframe, "start": start, "end": end}, 12)
    existing = get(db, ds_id)
    if existing:
        return existing
    sources = {pair: market.store.sources(exchange, pair, timeframe, start, end) for pair in pairs}
    fp = fingerprint(candles, val_end + step, end)
    db.execute(
        "INSERT INTO datasets (id, created_at, exchange, pairs, timeframe, start_ts, development_end_ts,"
        " validation_end_ts, end_ts, test_fingerprint, sources, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ds_id, now_ms(), exchange, to_json(pairs), timeframe, start, dev_end, val_end, end, fp, to_json(sources),
         f"split {split.development}/{split.validation}/{split.test}"))
    return get(db, ds_id)  # type: ignore[return-value]


def get(db: Database, ds_id: str) -> Dataset | None:
    row = db.query_one("SELECT * FROM datasets WHERE id = ?", (ds_id,))
    if not row:
        return None
    return Dataset(id=row["id"], exchange=row["exchange"], pairs=from_json(row["pairs"], []),
                   timeframe=row["timeframe"], start_ts=row["start_ts"], development_end_ts=row["development_end_ts"],
                   validation_end_ts=row["validation_end_ts"], end_ts=row["end_ts"],
                   test_fingerprint=row["test_fingerprint"], sources=from_json(row["sources"], {}))


def all_datasets(db: Database) -> list[Dataset]:
    return [d for d in (get(db, r["id"]) for r in db.query("SELECT id FROM datasets ORDER BY created_at DESC")) if d]


def verify_test_unchanged(ds: Dataset, market: MarketDataService) -> None:
    candles = load_candles(market, ds.exchange, ds.pairs, ds.timeframe)
    start, end = ds.window("test")
    if fingerprint(candles, start, end) != ds.test_fingerprint:
        raise TestSetLocked("the frozen test candles have changed since the dataset was registered; a final "
                            "evaluation on altered data would not be valid. Register a new dataset instead.")


def record_test_access(db: Database, ds: Dataset, experiment_id: str, strategies: list[str], reason: str,
                       actor: str) -> int:
    db.execute("INSERT INTO test_set_access (ts, dataset_id, experiment_id, strategies, reason, actor)"
               " VALUES (?, ?, ?, ?, ?, ?)", (now_ms(), ds.id, experiment_id, to_json(strategies), reason, actor))
    return int(db.scalar("SELECT COUNT(*) FROM test_set_access WHERE dataset_id = ?", (ds.id,)) or 0)
