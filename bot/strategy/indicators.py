"""Indicator math on pandas Series. Pure functions, no state."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    if period < 1:
        raise ValueError("period must be >= 1")
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI in [0, 100]. NaN until enough data exists."""
    if period < 1:
        raise ValueError("period must be >= 1")
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # No losses at all -> RSI is 100 by definition; no gains -> 0.
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out.clip(0.0, 100.0)


def donchian(df: pd.DataFrame, period: int, shift: int = 1) -> tuple[pd.Series, pd.Series]:
    """Highest high and lowest low of the previous `period` candles.

    Shifted by one candle by default: a breakout must clear the channel as it stood
    *before* this candle, otherwise the candle's own high defines the level it breaks.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    high = df["high"].rolling(period, min_periods=period).max()
    low = df["low"].rolling(period, min_periods=period).min()
    return high.shift(shift), low.shift(shift)


def bollinger(series: pd.Series, period: int, std: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Middle, upper and lower band. The middle band is a simple moving average."""
    if period < 2:
        raise ValueError("period must be >= 2")
    mid = series.rolling(period, min_periods=period).mean()
    dev = series.rolling(period, min_periods=period).std(ddof=0)
    return mid, mid + std * dev, mid - std * dev


def adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX: how strongly the market is trending, regardless of direction.

    Roughly: above 25 is a trend worth following, below 20 is a range.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    alpha = 1.0 / period
    tr = true_range(df).ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / tr.replace(0.0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / tr.replace(0.0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    a = df["high"] - df["low"]
    b = (df["high"] - prev_close).abs()
    c = (df["low"] - prev_close).abs()
    return pd.concat([a, b, c], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    if period < 1:
        raise ValueError("period must be >= 1")
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
