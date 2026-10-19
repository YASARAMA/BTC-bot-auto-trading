"""Building higher timeframes from lower ones, and aligning them without look-ahead.

A 1h candle built from 15m candles is only emitted when all four 15m candles exist: a
partial hour is not an hour. Blocks are aligned to UTC multiples of the target duration,
which is how Binance aligns its own candles (4h at 00:00, 04:00, ...; 1d at 00:00 UTC).

``align_higher`` attaches higher-timeframe values to base candles by *close time*: the 1h
block that closes at 10:00 becomes visible to the 15m candle that closes at 10:00, never to
the one that closes at 09:45. That single rule is what keeps multi-timeframe features free
of future information.
"""
from __future__ import annotations

import pandas as pd

from app.core.timeframes import can_derive, tf_ms
from app.data.validation import CANDLE_COLUMNS


def resample(df: pd.DataFrame, from_tf: str, to_tf: str) -> pd.DataFrame:
    if not can_derive(from_tf, to_tf):
        raise ValueError(f"cannot build {to_tf} candles from {from_tf} candles")
    if df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    step = tf_ms(to_tf)
    per = step // tf_ms(from_tf)
    work = df[CANDLE_COLUMNS].copy()
    work["block"] = (work["ts"] // step) * step
    grouped = work.groupby("block", sort=True)
    out = pd.DataFrame({
        "ts": grouped["ts"].first().index.astype("int64"),
        "open": grouped["open"].first().to_numpy(),
        "high": grouped["high"].max().to_numpy(),
        "low": grouped["low"].min().to_numpy(),
        "close": grouped["close"].last().to_numpy(),
        "volume": grouped["volume"].sum().to_numpy(),
        "n": grouped["ts"].count().to_numpy(),
    })
    complete = out[out["n"] == per].drop(columns="n").reset_index(drop=True)
    return complete


def align_higher(base: pd.DataFrame, base_tf: str, higher: pd.DataFrame, higher_tf: str,
                 columns: list[str], *, prefix: str, max_age_blocks: int = 2) -> pd.DataFrame:
    """Attach ``columns`` of ``higher`` to each row of ``base`` using only closed higher candles.

    A higher candle is usable from its close time onward. If the most recent usable higher
    candle is older than ``max_age_blocks`` (because of a data gap), the values are left
    empty (NaN) rather than carried forward indefinitely.
    """
    out = base.copy()
    if higher.empty or base.empty:
        for col in columns:
            out[f"{prefix}{col}"] = float("nan")
        return out
    h_step = tf_ms(higher_tf)
    left = pd.DataFrame({"close_time": base["ts"].to_numpy() + tf_ms(base_tf)})
    left["_order"] = range(len(left))
    right = higher[["ts", *columns]].copy()
    right["close_time"] = right["ts"] + h_step
    right = right.drop(columns="ts").sort_values("close_time")
    merged = pd.merge_asof(left.sort_values("close_time"), right, on="close_time", direction="backward",
                           tolerance=h_step * max_age_blocks, allow_exact_matches=True)
    merged = merged.sort_values("_order")
    for col in columns:
        out[f"{prefix}{col}"] = merged[col].to_numpy()
    return out
