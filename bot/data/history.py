"""Historical OHLCV: paginated download through ccxt with a CSV cache, plus a tolerant
CSV loader for offline data (data/samples/, exports from other tools)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from bot.common import timeframe_to_ms
from bot.data.feed import COLUMNS, rows_to_frame
from bot.execution.base import MarketData

log = logging.getLogger(__name__)

_TS_ALIASES = ("ts", "timestamp", "time", "date", "datetime", "open_time", "unix")
_VOL_ALIASES = ("volume_btc", "volume_base", "base_volume", "volume", "vol")


def load_csv(path: str | Path) -> pd.DataFrame:
    """Load OHLCV from CSV into the canonical frame (ts ms, open, high, low, close, volume)."""
    p = Path(path)
    # CryptoDataDownload files carry a one-line banner before the header.
    with p.open("r", encoding="utf-8") as fh:
        first = fh.readline()
    skip = 1 if ("http" in first.lower() and "," in first and "open" not in first.lower()) else 0
    raw = pd.read_csv(p, skiprows=skip)
    cols = {c: re.sub(r"[^a-z0-9_]", "", c.strip().lower().replace(" ", "_")) for c in raw.columns}
    raw = raw.rename(columns=cols)

    def pick(aliases: tuple[str, ...]) -> str:
        for a in aliases:
            if a in raw.columns:
                return a
        raise ValueError(f"{p}: no column among {aliases}; have {list(raw.columns)}")

    ts_col = pick(_TS_ALIASES)
    out = pd.DataFrame()
    out["ts"] = _to_epoch_ms(raw[ts_col])
    for c in ("open", "high", "low", "close"):
        out[c] = pd.to_numeric(raw[pick((c,))], errors="coerce")
    out["volume"] = pd.to_numeric(raw[pick(_VOL_ALIASES)], errors="coerce") if any(a in raw.columns for a in _VOL_ALIASES) else 0.0
    out = out.dropna(subset=["ts", "open", "high", "low", "close"])
    out["ts"] = out["ts"].astype("int64")
    out = out.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    if out.empty:
        raise ValueError(f"{p}: no usable rows")
    return out[COLUMNS]


def _to_epoch_ms(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        s = pd.to_numeric(series, errors="coerce").astype("float64")
        # seconds vs milliseconds: anything before year 2100 in seconds is < 4.1e9
        return s.where(s > 1e11, s * 1000.0)
    txt = series.astype(str).str.strip()
    # CryptoDataDownload style "2019-10-17 09-AM"
    ampm = txt.str.match(r"^\d{4}-\d{2}-\d{2} \d{1,2}-(AM|PM)$")
    if ampm.all():
        parsed = pd.to_datetime(txt, format="%Y-%m-%d %I-%p", utc=True)
    else:
        parsed = pd.to_datetime(txt, utc=True, errors="coerce", format="mixed")
    # Resolution-agnostic (pandas may parse to s/ms/us/ns): NaT becomes NaN and is dropped later.
    return (parsed - pd.Timestamp(0, tz="UTC")) / pd.Timedelta(milliseconds=1)


def save_csv(df: pd.DataFrame, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df[COLUMNS].to_csv(p, index=False)


def cache_path(cache_dir: str | Path, exchange_id: str, symbol: str, timeframe: str) -> Path:
    sym = re.sub(r"[^A-Za-z0-9]", "", symbol)
    return Path(cache_dir) / f"{exchange_id}_{sym}_{timeframe}.csv"


def download_ohlcv(
    market: MarketData,
    *,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
    page_limit: int = 1000,
    cache: Path | None = None,
) -> pd.DataFrame:
    """Download [since, until] with pagination. Merges with the cache file when given."""
    tf = timeframe_to_ms(timeframe)
    cached = load_csv(cache) if cache and cache.exists() else pd.DataFrame(columns=COLUMNS)
    have = set(cached["ts"].tolist()) if len(cached) else set()
    cursor = since_ms
    frames = [cached] if len(cached) else []
    while cursor <= until_ms:
        # Skip ranges already cached.
        if cursor in have:
            nxt = cursor
            while nxt in have:
                nxt += tf
            cursor = nxt
            if cursor > until_ms:
                break
        rows = market.fetch_ohlcv(symbol, timeframe, cursor, page_limit)
        if not rows:
            break
        frame = rows_to_frame(rows)
        frames.append(frame)
        last = int(frame["ts"].iloc[-1])
        log.info("downloaded %d candles up to %s", len(frame), pd.to_datetime(last, unit="ms", utc=True))
        if last < cursor:
            break
        cursor = last + tf
    if not frames:
        raise RuntimeError("no candles downloaded")
    df = pd.concat(frames, ignore_index=True).drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    if cache is not None:
        save_csv(df, cache)
    return df[(df["ts"] >= since_ms) & (df["ts"] <= until_ms)].reset_index(drop=True)
