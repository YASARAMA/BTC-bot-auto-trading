"""Strategy B - momentum breakout from a defined range, confirmed by a completed candle."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from app.indicators import donchian
from app.regimes.detector import BREAKOUT, TREND_UP
from app.strategies.base import (SignalFrame, Strategy, StrategyContext, StrategyParams, cost_ok, slowest_down)


class BreakoutParams(StrategyParams):
    range_lookback: int = Field(48, ge=5, le=500, description="candles that define the range being broken")
    min_range_width_atr: float = Field(2.0, ge=0, le=50)
    max_range_width_atr: float = Field(8.0, gt=0, le=100, description="wider than this is a trend leg, not a range")
    breakout_buffer_atr: float = Field(0.1, ge=0, le=3)
    rel_volume_min: float = Field(1.5, ge=0, le=20)
    close_location_min: float = Field(0.7, ge=0, le=1, description="close in the top part of the breakout candle")
    rsi_min: float = Field(55.0, ge=0, le=100)
    rsi_max: float = Field(80.0, ge=0, le=100)
    max_extension_atr: float = Field(1.5, gt=0, le=10, description="skip if most of the move already happened")
    max_bar_range_atr: float = Field(3.5, gt=0, le=20, description="skip chasing a single huge candle")
    min_avg_quote_volume: float = Field(1_000_000.0, ge=0, description="liquidity floor per candle, quote currency")
    stop_buffer_atr: float = Field(0.5, ge=0, le=5)
    min_stop_atr: float = Field(0.8, gt=0, le=5)
    max_stop_atr: float = Field(3.0, gt=0, le=10)
    target_r: float = Field(2.5, gt=0, le=10)
    trail_lookback: int = Field(10, ge=2, le=200, description="exit when the close loses this many candles' low")
    time_stop_bars: int = Field(64, ge=1, le=2000)
    min_reward_cost_ratio: float = Field(3.0, ge=0, le=50)

    @model_validator(mode="after")
    def _order(self) -> "BreakoutParams":
        if self.min_range_width_atr >= self.max_range_width_atr:
            raise ValueError("min_range_width_atr must be below max_range_width_atr")
        if self.rsi_min >= self.rsi_max:
            raise ValueError("rsi_min must be below rsi_max")
        return self


class MomentumBreakout(Strategy):
    name = "momentum_breakout"
    title = "B · Momentum breakout"
    code_version = "1.0"
    hypothesis = ("A completed close beyond a well-defined consolidation, on above-average volume and with "
                  "momentum that is not yet exhausted, continues far enough to pay for false breakouts.")
    required_regimes = (TREND_UP, BREAKOUT)
    entry_rules = (
        "regime is trend_up or an upward high_volatility_breakout",
        "the slowest confirmation timeframe is not in a downtrend",
        "a defined range: its width is between the min and max ATR multiples",
        "completed candle closes above the range high plus a buffer",
        "relative volume at or above the minimum",
        "the candle closes in the upper part of its range",
        "RSI shows momentum without being exhausted",
        "not more than max_extension_atr above the range high (move not already spent)",
        "the breakout candle is not an outsized spike",
        "average traded value per candle is above the liquidity floor",
        "stop distance within limits and the target pays the costs",
    )
    exit_rules = ("close below the trailing low of trail_lookback candles", "time stop", "hard stop or take profit")
    stop_rule = "range high minus stop_buffer_atr ATR (back inside the range = failed breakout), at least min_stop_atr away"
    target_rule = "entry + target_r x initial risk"
    Params = BreakoutParams
    tunable = ("range_lookback", "rel_volume_min", "max_extension_atr", "target_r", "trail_lookback")

    def compute(self, f: pd.DataFrame, regime: pd.DataFrame, ctx: StrategyContext) -> SignalFrame:
        p: BreakoutParams = self.params  # type: ignore[assignment]
        close, atr = f["close"], f["atr"]
        dc = donchian(f, p.range_lookback)
        r_high, r_low = dc["dc_high"], dc["dc_low"]
        width = (r_high - r_low) / atr
        reg_ok = (regime["regime"] == TREND_UP) | ((regime["regime"] == BREAKOUT) & (regime["regime_direction"] > 0))
        htf_ok = ~slowest_down(f, ctx.confirm_timeframes)
        defined = (width >= p.min_range_width_atr) & (width <= p.max_range_width_atr)
        broke = close > r_high + p.breakout_buffer_atr * atr
        volume = f["rel_volume"] >= p.rel_volume_min
        strong_close = f["close_loc"] >= p.close_location_min
        momentum = (f["rsi"] >= p.rsi_min) & (f["rsi"] <= p.rsi_max)
        fresh = (close - r_high) <= p.max_extension_atr * atr
        sane = f["range_atr"] <= p.max_bar_range_atr
        liquid = f["avg_quote_volume"] >= p.min_avg_quote_volume

        stop = r_high - p.stop_buffer_atr * atr
        stop = np.minimum(stop, close - p.min_stop_atr * atr)
        stop_ok = (close - stop) <= p.max_stop_atr * atr
        target = close + p.target_r * (close - stop)
        levels_ok = stop_ok & cost_ok(close, target, ctx, p.min_reward_cost_ratio)
        conditions = dict(zip(self.entry_rules, (reg_ok, htf_ok, defined, broke, volume, strong_close, momentum,
                                                 fresh, sane, liquid, levels_ok)))
        entry = pd.concat(conditions.values(), axis=1).fillna(False).all(axis=1)
        confidence = (0.45 + 0.25 * regime["regime_confidence"].fillna(0)
                      + 0.15 * ((f["rel_volume"] - p.rel_volume_min) / 2).clip(0, 1)
                      + 0.15 * (1 - ((close - r_high) / (p.max_extension_atr * atr)).clip(0, 1)))
        trail = f["low"].shift(1).rolling(p.trail_lookback, min_periods=p.trail_lookback).min()
        exit_signal = close < trail
        exit_reason = pd.Series(np.where(exit_signal, f"close below the {p.trail_lookback}-candle trailing low", ""),
                                index=f.index)
        return SignalFrame(entry=entry, stop=pd.Series(stop, index=f.index), target=target,
                           invalidation=pd.Series(np.nan, index=f.index), confidence=confidence,
                           exit_signal=exit_signal, exit_reason=exit_reason, conditions=conditions)

    def time_stop_bars(self) -> int:
        return self.params.time_stop_bars  # type: ignore[attr-defined]
