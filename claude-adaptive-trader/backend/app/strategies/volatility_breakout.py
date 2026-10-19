"""Strategy D - volatility breakout: compression followed by a confirmed directional expansion."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from app.regimes.detector import BREAKOUT
from app.strategies.base import (SignalFrame, Strategy, StrategyContext, StrategyParams, cost_ok, slowest_down)


class VolatilityParams(StrategyParams):
    box_lookback: int = Field(12, ge=3, le=200, description="candles forming the compression box")
    max_box_width_atr: float = Field(3.0, gt=0, le=20, description="the box must be this tight")
    expansion_range_atr: float = Field(1.5, gt=0, le=10, description="breakout candle range vs previous ATR")
    breakout_buffer_atr: float = Field(0.1, ge=0, le=3)
    rel_volume_min: float = Field(1.3, ge=0, le=20)
    close_location_min: float = Field(0.6, ge=0, le=1)
    max_bar_range_atr: float = Field(4.0, gt=0, le=20, description="beyond this the move is unstable, skip")
    max_extension_atr: float = Field(1.5, gt=0, le=10)
    stop_box_fraction: float = Field(0.5, ge=0, le=1, description="stop at this fraction of the box from its low")
    stop_buffer_atr: float = Field(0.25, ge=0, le=3)
    min_stop_atr: float = Field(0.8, gt=0, le=5)
    max_stop_atr: float = Field(3.5, gt=0, le=10)
    invalidation_buffer_atr: float = Field(0.5, ge=0, le=5, description="close this far back inside the box = failed")
    target_r: float = Field(2.0, gt=0, le=10)
    time_stop_bars: int = Field(48, ge=1, le=2000)
    min_reward_cost_ratio: float = Field(3.0, ge=0, le=50)

    @model_validator(mode="after")
    def _consistent(self) -> "VolatilityParams":
        # The widest stop a qualifying setup can need: the allowed extension above the box,
        # plus the part of the box above the stop, plus the buffer. If the stop limit were
        # tighter than that, it would silently veto setups that pass every other rule.
        worst = (self.max_extension_atr + (1 - self.stop_box_fraction) * self.max_box_width_atr
                 + self.stop_buffer_atr + self.breakout_buffer_atr)
        if self.max_stop_atr + 1e-9 < worst:
            raise ValueError(f"max_stop_atr {self.max_stop_atr} is tighter than the widest stop a qualifying "
                             f"setup needs ({worst:.2f} ATR); raise it or tighten the box/extension limits")
        return self


class VolatilityBreakout(Strategy):
    name = "volatility_breakout"
    title = "D · Volatility breakout"
    code_version = "1.0"
    hypothesis = ("Periods of compressed volatility end in expansion; a completed candle that leaves a tight box "
                  "with range expansion and volume shows the direction, and the expansion tends to carry.")
    required_regimes = (BREAKOUT,)
    entry_rules = (
        "regime is an upward high_volatility_breakout",
        "the slowest confirmation timeframe is not in a downtrend",
        "a squeeze occurred recently (Bollinger width in its lowest percentiles)",
        "the compression box is tight",
        "the breakout candle's range expanded versus the previous ATR",
        "completed close above the box high plus a buffer",
        "relative volume confirms",
        "the candle closes in its upper part",
        "the candle is not an unstable spike and the move is not over-extended",
        "stop distance within limits and the target pays the costs",
    )
    exit_rules = ("close falls back inside the box (failed breakout)", "time stop", "hard stop or take profit")
    stop_rule = "inside the box at stop_box_fraction of its height minus a buffer; at least min_stop_atr away"
    target_rule = "entry + target_r x initial risk"
    Params = VolatilityParams
    tunable = ("box_lookback", "max_box_width_atr", "expansion_range_atr", "target_r", "time_stop_bars")

    def compute(self, f: pd.DataFrame, regime: pd.DataFrame, ctx: StrategyContext) -> SignalFrame:
        p: VolatilityParams = self.params  # type: ignore[assignment]
        close, atr = f["close"], f["atr"]
        prev_atr = atr.shift(1)
        box_high = f["high"].shift(1).rolling(p.box_lookback, min_periods=p.box_lookback).max()
        box_low = f["low"].shift(1).rolling(p.box_lookback, min_periods=p.box_lookback).min()
        reg_ok = (regime["regime"] == BREAKOUT) & (regime["regime_direction"] > 0)
        htf_ok = ~slowest_down(f, ctx.confirm_timeframes)
        squeeze = f["squeeze_recent"].fillna(False).astype(bool)
        tight = (box_high - box_low) <= p.max_box_width_atr * prev_atr
        expanded = f["range_atr"] >= p.expansion_range_atr
        broke = close > box_high + p.breakout_buffer_atr * atr
        volume = f["rel_volume"] >= p.rel_volume_min
        strong_close = f["close_loc"] >= p.close_location_min
        stable = (f["range_atr"] <= p.max_bar_range_atr) & ((close - box_high) <= p.max_extension_atr * atr)
        stop = box_low + p.stop_box_fraction * (box_high - box_low) - p.stop_buffer_atr * atr
        stop = np.minimum(stop, close - p.min_stop_atr * atr)
        stop_ok = (close - stop) <= p.max_stop_atr * atr
        target = close + p.target_r * (close - stop)
        levels_ok = stop_ok & cost_ok(close, target, ctx, p.min_reward_cost_ratio)
        conditions = dict(zip(self.entry_rules, (reg_ok, htf_ok, squeeze, tight, expanded, broke, volume,
                                                 strong_close, stable, levels_ok)))
        entry = pd.concat(conditions.values(), axis=1).fillna(False).all(axis=1)
        confidence = (0.45 + 0.3 * regime["regime_confidence"].fillna(0)
                      + 0.25 * ((f["rel_volume"] - p.rel_volume_min) / 2).clip(0, 1))
        invalidation = box_high - p.invalidation_buffer_atr * atr
        # The state-based exit for D is the invalidation level carried from the entry candle.
        none = pd.Series(False, index=f.index)
        return SignalFrame(entry=entry, stop=pd.Series(stop, index=f.index), target=target, invalidation=invalidation,
                           confidence=confidence, exit_signal=none, exit_reason=pd.Series("", index=f.index),
                           conditions=conditions)

    def time_stop_bars(self) -> int:
        return self.params.time_stop_bars  # type: ignore[attr-defined]
