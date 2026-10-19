"""Bollinger Bands: SMA plus/minus ``k`` population standard deviations (TradingView's ``ta.stdev``)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def bollinger(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.DataFrame:
    c = close.astype("float64")
    mid = c.rolling(period, min_periods=period).mean()
    sd = c.rolling(period, min_periods=period).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    with np.errstate(divide="ignore", invalid="ignore"):
        width = (upper - lower) / mid
        pct_b = (c - lower) / (upper - lower)
    pct_b = pct_b.where(upper != lower, 0.5)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width, "bb_pct": pct_b,
                         "bb_std": sd})
