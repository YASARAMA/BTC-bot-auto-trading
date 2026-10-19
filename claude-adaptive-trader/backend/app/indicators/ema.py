"""Moving averages.

Conventions follow TradingView, which is what Binance's own charts use, so a value on this
platform's chart can be checked against Binance by eye:

* ``sma``: plain rolling mean, empty until ``period`` values exist.
* ``ema``: ``alpha = 2 / (period + 1)``, seeded with the SMA of the first ``period`` values.
* ``rma`` (Wilder's smoothing, used by RSI, ATR and ADX): ``alpha = 1 / period``, same seed.

All of them are causal: the value at row ``t`` depends on rows ``<= t`` only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    if period < 1:
        raise ValueError("period must be >= 1")
    return series.astype("float64").rolling(period, min_periods=period).mean()


def _seeded_ewm(series: pd.Series, period: int, alpha: float) -> pd.Series:
    if period < 1:
        raise ValueError("period must be >= 1")
    values = series.astype("float64")
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    valid = values.notna().to_numpy()
    if not valid.any():
        return out
    first = int(np.argmax(valid))
    seed_end = first + period - 1
    if seed_end >= len(values):
        return out
    window = values.iloc[first:seed_end + 1]
    if window.isna().any():
        # A hole inside the seed window: start again after it rather than invent values.
        last_nan = int(np.nonzero(window.isna().to_numpy())[0][-1]) + first
        tail = _seeded_ewm(values.iloc[last_nan + 1:], period, alpha)
        out.iloc[last_nan + 1:] = tail.to_numpy()
        return out
    seeded = values.iloc[seed_end:].copy()
    seeded.iloc[0] = window.mean()
    out.iloc[seed_end:] = seeded.ewm(alpha=alpha, adjust=False, ignore_na=False).mean().to_numpy()
    return out


def ema(series: pd.Series, period: int) -> pd.Series:
    return _seeded_ewm(series, period, 2.0 / (period + 1.0))


def rma(series: pd.Series, period: int) -> pd.Series:
    return _seeded_ewm(series, period, 1.0 / period)
