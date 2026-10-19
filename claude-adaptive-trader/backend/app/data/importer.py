"""Importing candles from local CSV files.

Two layouts are understood:

* A header row with ``ts`` (or ``timestamp``/``open_time``/``time``/``date``), ``open``, ``high``,
  ``low``, ``close`` and ``volume``.
* Binance's own kline archives (data.binance.vision): no header, twelve columns, the first
  six of which are open time, open, high, low, close and volume. Recent archives use
  microsecond timestamps; those are converted.

The caller states which exchange, pair and timeframe a file holds. The file is validated
like a download, stored under that exchange's name, and labelled ``local-file:<name>``.
Nothing here guesses that a file is Binance data: a Coinbase file imported as Coinbase stays
Coinbase.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.core.clock import now_ms, parse_ts
from app.core.errors import DataValidationError, InvalidRequest
from app.core.timeframes import validate_timeframe
from app.data.historical_store import CandleStore
from app.data.validation import CANDLE_COLUMNS, validate_candles
from app.monitoring.logging import get_logger, log_event

log = get_logger("importer")

MAX_IMPORT_BYTES = 300 * 1024 * 1024
_TS_NAMES = ("ts", "timestamp", "open_time", "time", "date", "datetime", "unix")
_ALLOWED_EXCHANGES = {"binance", "binanceus", "coinbase", "kraken", "bitstamp", "bybit", "okx", "other"}

# The real market-data samples that ship with this repository (see data/samples/README).
REPO_SAMPLES = [
    {"file": "binance_BTCUSDT_1h_2020-11_2021-05.csv", "exchange": "binance", "pair": "BTC/USDT", "timeframe": "1h"},
    {"file": "coinbase_BTCUSD_1h_2017-07_2019-10.csv", "exchange": "coinbase", "pair": "BTC/USD", "timeframe": "1h"},
]


def read_candle_csv(content: bytes | str | Path) -> pd.DataFrame:
    if isinstance(content, Path):
        if content.stat().st_size > MAX_IMPORT_BYTES:
            raise InvalidRequest("file is larger than the 300 MB import limit")
        raw = content.read_bytes()
    elif isinstance(content, str):
        raw = content.encode("utf-8")
    else:
        raw = content
    if len(raw) > MAX_IMPORT_BYTES:
        raise InvalidRequest("file is larger than the 300 MB import limit")
    text = raw.decode("utf-8-sig", errors="strict")
    first = text.splitlines()[0] if text.strip() else ""
    if not first:
        raise DataValidationError("the file is empty")
    first_field = first.split(",")[0].strip().strip('"')
    try:
        float(first_field)
        has_header = False
    except ValueError:
        has_header = True
    if has_header:
        df = pd.read_csv(io.StringIO(text))
        df.columns = [str(c).strip().lower() for c in df.columns]
        ts_col = next((c for c in _TS_NAMES if c in df.columns), None)
        missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
        if ts_col is None or missing:
            raise DataValidationError(
                "the header must name a time column (ts/timestamp/open_time/time/date) and "
                f"open, high, low, close; missing: {missing or ['time column']}")
        if "volume" not in df.columns:
            raise DataValidationError("the file has no volume column; volume is required and is never invented")
        out = df[[ts_col, "open", "high", "low", "close", "volume"]].copy()
        out.columns = CANDLE_COLUMNS
    else:
        df = pd.read_csv(io.StringIO(text), header=None)
        if df.shape[1] < 6:
            raise DataValidationError("a header-less file needs at least six columns (Binance kline layout)")
        out = df.iloc[:, :6].copy()
        out.columns = CANDLE_COLUMNS
    out["ts"] = _to_ms(out["ts"])
    return out


def _to_ms(col: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(col, errors="coerce")
    if numeric.notna().all():
        values = numeric.to_numpy(dtype="float64")
        # Seconds, milliseconds or microseconds, judged per row by magnitude.
        ms = np.where(values > 1e14, values / 1000.0, np.where(values < 1e11, values * 1000.0, values))
        return pd.Series(ms.round().astype("int64"), index=col.index)
    try:
        return col.map(lambda v: parse_ts(str(v))).astype("int64")
    except Exception as exc:  # noqa: BLE001 - report any unparseable value the same way
        raise DataValidationError(f"could not read the time column: {exc}") from None


def import_frame(store: CandleStore, df: pd.DataFrame, *, exchange: str, pair: str, timeframe: str,
                 source: str) -> dict[str, Any]:
    exchange = exchange.strip().lower()
    if exchange not in _ALLOWED_EXCHANGES:
        raise InvalidRequest(f"unknown exchange {exchange!r}; use one of {sorted(_ALLOWED_EXCHANGES)}")
    validate_timeframe(timeframe)
    if "/" not in pair:
        raise InvalidRequest("pair must look like BTC/USDT")
    clean, report = validate_candles(df, timeframe, now_ms=now_ms())
    if clean.empty:
        raise DataValidationError("no valid candles in the file", details={"report": report.to_dict()})
    written = store.upsert(exchange, pair, timeframe, clean, source)
    store.set_meta(exchange, pair, timeframe, status="imported", report=report.to_dict())
    log_event(log, 20, f"imported {written} {exchange} {pair} {timeframe} candles from {source}",
              issues=report.issues())
    return {"exchange": exchange, "pair": pair, "timeframe": timeframe, "source": source, "written": written,
            "report": report.to_dict()}


def import_csv(store: CandleStore, content: bytes | str | Path, *, filename: str, exchange: str, pair: str,
               timeframe: str) -> dict[str, Any]:
    df = read_candle_csv(content)
    safe_name = Path(filename).name[:120] or "upload.csv"
    return import_frame(store, df, exchange=exchange, pair=pair, timeframe=timeframe,
                        source=f"local-file:{safe_name}")


def samples_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "data" / "samples"


def import_repo_samples(store: CandleStore) -> list[dict[str, Any]]:
    """Import the real candle files that ship with this repository, with their true labels."""
    results = []
    base = samples_dir()
    for spec in REPO_SAMPLES:
        path = base / spec["file"]
        if not path.exists():
            results.append({**spec, "error": f"{path} not found"})
            continue
        results.append(import_csv(store, path, filename=spec["file"], exchange=spec["exchange"],
                                  pair=spec["pair"], timeframe=spec["timeframe"]))
    return results
