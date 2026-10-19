"""Volume features.

Relative volume compares a candle's volume with the average of the ``period`` candles
*before* it, so a single huge candle does not dilute its own baseline. The result is empty
until that history exists, and also when the baseline is zero (no volume to compare with).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    return volume.astype("float64").rolling(period, min_periods=period).mean()


def relative_volume(volume: pd.Series, period: int = 20) -> pd.Series:
    v = volume.astype("float64")
    baseline = v.shift(1).rolling(period, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = v / baseline
    return rel.where(baseline > 0)


def quote_volume(df: pd.DataFrame) -> pd.Series:
    """Approximate traded value of each candle (volume times the typical price)."""
    typical = (df["high"].astype("float64") + df["low"].astype("float64") + df["close"].astype("float64")) / 3.0
    return df["volume"].astype("float64") * typical
