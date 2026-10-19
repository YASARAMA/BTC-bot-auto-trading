"""Relative Strength Index (Wilder), TradingView convention.

RSI = 100 - 100 / (1 + RMA(gains) / RMA(losses)). When the average loss is zero the RSI is
100, and when the average gain is zero it is 0, exactly as TradingView defines it. The first
value appears once ``period`` price changes exist.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.ema import rma


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.astype("float64").diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    up = rma(gains, period)
    down = rma(losses, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        value = 100.0 - 100.0 / (1.0 + up / down)
    value = value.where(down != 0, 100.0)
    value = value.where(~((up == 0) & (down != 0)), 0.0)
    return value.where(up.notna() & down.notna())
