"""Price-structure helpers: channels, percentile ranks, slopes and candle shape."""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def donchian(df: pd.DataFrame, period: int, *, shift: int = 1) -> pd.DataFrame:
    """Highest high and lowest low of the ``period`` candles before the current one (``shift=1``)."""
    high = df["high"].astype("float64").shift(shift)
    low = df["low"].astype("float64").shift(shift)
    return pd.DataFrame({
        "dc_high": high.rolling(period, min_periods=period).max(),
        "dc_low": low.rolling(period, min_periods=period).min(),
    })


def percentile_rank(series: pd.Series, window: int) -> pd.Series:
    """Share (0-100) of the previous ``window - 1`` values that are <= the current value.

    Uses only the current value and the ones before it. Empty until the window is full of
    valid numbers.
    """
    values = series.astype("float64").to_numpy()
    n = len(values)
    out = np.full(n, np.nan)
    if window < 2 or n < window:
        return pd.Series(out, index=series.index)
    chunk = 20_000
    for start in range(0, n - window + 1, chunk):
        stop = min(n - window + 1, start + chunk)
        block = values[start:stop + window - 1]
        windows = sliding_window_view(block, window)
        current = windows[:, -1:]
        previous = windows[:, :-1]
        valid = ~np.isnan(windows).any(axis=1)
        ranks = (previous <= current).mean(axis=1) * 100.0
        ranks[~valid] = np.nan
        out[start + window - 1:stop + window - 1] = ranks
    return pd.Series(out, index=series.index)


def change(series: pd.Series, lookback: int) -> pd.Series:
    return series.astype("float64") - series.astype("float64").shift(lookback)


def roc(close: pd.Series, lookback: int) -> pd.Series:
    c = close.astype("float64")
    return (c / c.shift(lookback) - 1.0) * 100.0


def close_location(df: pd.DataFrame) -> pd.Series:
    """Where the close sits inside the candle's range: 0 at the low, 1 at the high, 0.5 for no range."""
    high, low, close = (df[c].astype("float64") for c in ("high", "low", "close"))
    span = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        loc = (close - low) / span
    return loc.where(span > 0, 0.5)
