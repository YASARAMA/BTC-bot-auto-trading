"""Directional Movement Index and ADX (Wilder), matching TradingView's ``ta.dmi``.

+DM = up move if it beats the down move and is positive, else 0 (and symmetrically -DM).
+DI = 100 * RMA(+DM) / RMA(TR); DX = 100 * |+DI - -DI| / (+DI + -DI); ADX = RMA(DX).
The first candle has no previous candle, so the series start one row later; the first ADX
value appears at row ``2 * period - 1``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.atr import true_range
from app.indicators.ema import rma


def dmi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high, low = df["high"].astype("float64"), df["low"].astype("float64")
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    plus_dm[up.isna()] = np.nan
    minus_dm[down.isna()] = np.nan
    tr = true_range(df, first_bar=False)
    tr_s = rma(tr, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * rma(plus_dm, period) / tr_s
        minus_di = 100.0 * rma(minus_dm, period) / tr_s
    plus_di = plus_di.where(tr_s != 0, 0.0).where(tr_s.notna())
    minus_di = minus_di.where(tr_s != 0, 0.0).where(tr_s.notna())
    total = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / total.where(total != 0, 1.0)
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "dx": dx, "adx": rma(dx, period)})


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return dmi(df, period)["adx"]
