"""True range and Average True Range (Wilder smoothing, TradingView convention)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.ema import rma


def true_range(df: pd.DataFrame, *, first_bar: bool = True) -> pd.Series:
    """max(high - low, |high - previous close|, |low - previous close|).

    The first candle has no previous close: with ``first_bar=True`` its range is
    ``high - low`` (TradingView's ``ta.tr(true)``), otherwise it is empty.
    """
    high, low, close = (df[c].astype("float64") for c in ("high", "low", "close"))
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1, skipna=False)
    if first_bar and len(tr):
        tr.iloc[0] = float(high.iloc[0] - low.iloc[0])
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return rma(true_range(df, first_bar=True), period)


def atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR as a percentage of the close, comparable across prices and pairs."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return atr(df, period) / df["close"].astype("float64") * 100.0
