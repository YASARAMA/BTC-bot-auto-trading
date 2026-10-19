"""Every market feature the regime detector and the strategies use, computed once.

All columns are causal: the value in row ``t`` is computed from candles ``<= t`` only.
Higher-timeframe columns (``h1h_trend``, ``h4h_trend``, ...) come from complete higher
candles built from the base candles and aligned by close time, so a 15m row never sees an
hour that had not finished. The no-look-ahead tests recompute this frame on truncated
histories and require identical rows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import RegimeConfig
from app.data.resample import align_higher, resample
from app.indicators import (atr, bollinger, close_location, dmi, donchian, ema, percentile_rank, quote_volume,
                            relative_volume, rsi, true_range)

BASE_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def htf_trend(candles: pd.DataFrame) -> pd.DataFrame:
    """Trend state of a higher timeframe: +1 up, -1 down, 0 mixed, NaN while warming up."""
    out = candles[["ts", "close"]].copy()
    fast = ema(candles["close"], 20)
    mid = ema(candles["close"], 50)
    up = (candles["close"] > mid) & (fast > mid)
    down = (candles["close"] < mid) & (fast < mid)
    trend = np.where(up, 1.0, np.where(down, -1.0, 0.0))
    out["trend"] = np.where(mid.isna(), np.nan, trend)
    out["ema50"] = mid
    return out


def build_features(candles: pd.DataFrame, timeframe: str, confirm_timeframes: list[str],
                   cfg: RegimeConfig) -> pd.DataFrame:
    df = candles[BASE_COLUMNS].reset_index(drop=True).copy()
    df["ts"] = df["ts"].astype("int64")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype("float64")
    close = df["close"]
    f = df.copy()
    f["ema_fast"] = ema(close, cfg.ema_fast)
    f["ema_mid"] = ema(close, cfg.ema_mid)
    f["ema_slow"] = ema(close, cfg.ema_slow)
    f["atr"] = atr(df, cfg.atr_period)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["atr_pct"] = f["atr"] / close * 100.0
    f["atr_rank"] = percentile_rank(f["atr_pct"], cfg.rank_window)
    d = dmi(df, cfg.adx_period)
    f["adx"], f["plus_di"], f["minus_di"] = d["adx"], d["plus_di"], d["minus_di"]
    f["rsi"] = rsi(close, cfg.rsi_period)
    bb = bollinger(close, cfg.bollinger_period, cfg.bollinger_std)
    for col in ("bb_mid", "bb_upper", "bb_lower", "bb_width", "bb_std"):
        f[col] = bb[col]
    f["bb_width_rank"] = percentile_rank(f["bb_width"], cfg.rank_window)
    f["rel_volume"] = relative_volume(df["volume"], cfg.volume_period)
    qv = quote_volume(df)
    f["quote_volume"] = qv
    f["avg_quote_volume"] = qv.shift(1).rolling(cfg.volume_period, min_periods=cfg.volume_period).mean()
    dc = donchian(df, cfg.range_period)
    f["range_high"], f["range_low"] = dc["dc_high"], dc["dc_low"]
    with np.errstate(divide="ignore", invalid="ignore"):
        f["range_width_atr"] = (f["range_high"] - f["range_low"]) / f["atr"]
        f["slope_mid_atr"] = (f["ema_mid"] - f["ema_mid"].shift(cfg.slope_lookback)) / f["atr"]
        # A candle's range against the ATR *before* it, so an expanding candle does not
        # dilute its own yardstick.
        f["range_atr"] = (df["high"] - df["low"]) / f["atr"].shift(1)
        f["extension_atr"] = (close - f["ema_fast"]) / f["atr"]
    f["true_range"] = true_range(df)
    f["close_loc"] = close_location(df)
    f["squeeze_recent"] = (f["bb_width_rank"].rolling(cfg.squeeze_lookback, min_periods=cfg.squeeze_lookback).min()
                           <= cfg.squeeze_percentile)
    for tf in confirm_timeframes:
        higher = resample(df, timeframe, tf)
        trend = htf_trend(higher) if not higher.empty else pd.DataFrame(columns=["ts", "close", "trend", "ema50"])
        f = align_higher(f, timeframe, trend, tf, ["trend", "ema50"], prefix=f"h{tf}_")
    return f


def htf_columns(confirm_timeframes: list[str]) -> list[str]:
    return [f"h{tf}_trend" for tf in confirm_timeframes]
