"""Strategy C - mean reversion, only inside an independently validated range regime."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from app.regimes.detector import BREAKOUT, RANGE, TREND_DOWN
from app.strategies.base import (SignalFrame, Strategy, StrategyContext, StrategyParams, cost_ok, slowest_down)


class MeanReversionParams(StrategyParams):
    range_context_bars: int = Field(10, ge=2, le=200, description="candles before the stretch that must show a range")
    min_range_share: float = Field(0.5, ge=0, le=1, description="share of those candles classified as range")
    entry_z: float = Field(-2.0, ge=-5, le=-0.5, description="previous close this many std below the mean")
    rsi_max: float = Field(40.0, ge=5, le=50,
                           description="supporting evidence only: previous candle's RSI at or below this")
    stop_buffer_atr: float = Field(0.5, ge=0, le=5)
    min_stop_atr: float = Field(0.6, gt=0, le=5)
    max_stop_atr: float = Field(2.5, gt=0, le=10)
    min_reward_risk: float = Field(1.0, gt=0, le=10, description="distance to the mean versus distance to the stop")
    time_stop_bars: int = Field(32, ge=1, le=2000)
    min_reward_cost_ratio: float = Field(3.0, ge=0, le=50)


class MeanReversion(Strategy):
    name = "mean_reversion"
    title = "C · Mean reversion (range only)"
    code_version = "1.0"
    hypothesis = ("Inside a validated range (low ADX, flat mean, no expansion), a close stretched two standard "
                  "deviations below the mean that is followed by a close back inside the band tends to revert "
                  "towards the mean before reaching a stop placed below the extreme.")
    required_regimes = (RANGE,)
    entry_rules = (
        "regime is range on the signal candle",
        "the range was established before the stretch (most of the preceding candles were range)",
        "the slowest confirmation timeframe is not in a downtrend",
        "previous close was stretched below the mean by at least entry_z standard deviations",
        "previous candle's RSI confirms weakness (supporting, not a standalone signal)",
        "completed candle closes back above the lower band and is bullish",
        "stop distance within the ATR limits",
        "distance to the mean is at least min_reward_risk times the risk and pays the costs",
    )
    exit_rules = ("close reaches the mean (middle band)", "regime turns trend_down or high_volatility_breakout",
                  "time stop", "hard stop or take profit")
    stop_rule = "below the lower of the last two lows minus stop_buffer_atr ATR"
    target_rule = "the middle Bollinger band (the mean) at the signal candle"
    Params = MeanReversionParams
    tunable = ("entry_z", "rsi_max", "min_reward_risk", "time_stop_bars", "min_range_share")

    def compute(self, f: pd.DataFrame, regime: pd.DataFrame, ctx: StrategyContext) -> SignalFrame:
        p: MeanReversionParams = self.params  # type: ignore[assignment]
        close, atr = f["close"], f["atr"]
        with np.errstate(divide="ignore", invalid="ignore"):
            z = (close - f["bb_mid"]) / f["bb_std"]
        in_range = regime["regime"] == RANGE
        # The stretch candle itself usually leaves the range, so the range must be validated
        # on the candles before it, independently of the move being traded.
        share = ((regime["regime"] == RANGE).astype(float).shift(2)
                 .rolling(p.range_context_bars, min_periods=p.range_context_bars).mean())
        established = share >= p.min_range_share
        htf_ok = ~slowest_down(f, ctx.confirm_timeframes)
        stretched = z.shift(1) <= p.entry_z
        oversold = f["rsi"].shift(1) <= p.rsi_max
        reentry = (close > f["bb_lower"]) & (close > f["open"])
        extreme = np.minimum(f["low"], f["low"].shift(1))
        stop = np.minimum(extreme - p.stop_buffer_atr * atr, close - p.min_stop_atr * atr)
        stop_ok = (close - stop) <= p.max_stop_atr * atr
        target = f["bb_mid"]
        reward_ok = ((target - close) >= p.min_reward_risk * (close - stop)) & cost_ok(close, target, ctx,
                                                                                         p.min_reward_cost_ratio)
        conditions = dict(zip(self.entry_rules, (in_range, established, htf_ok, stretched, oversold, reentry, stop_ok,
                                                 reward_ok)))
        entry = pd.concat(conditions.values(), axis=1).fillna(False).all(axis=1)
        confidence = 0.45 + 0.35 * regime["regime_confidence"].fillna(0) + 0.2 * ((-z.shift(1) - 2) / 1.5).clip(0, 1)
        at_mean = close >= f["bb_mid"]
        hostile = regime["regime"].isin([TREND_DOWN, BREAKOUT])
        exit_signal = at_mean | hostile
        exit_reason = pd.Series(np.where(at_mean, "close reached the mean",
                                         np.where(hostile, "regime is no longer a range", "")), index=f.index)
        return SignalFrame(entry=entry, stop=pd.Series(stop, index=f.index), target=target,
                           invalidation=pd.Series(np.nan, index=f.index), confidence=confidence,
                           exit_signal=exit_signal, exit_reason=exit_reason, conditions=conditions)

    def time_stop_bars(self) -> int:
        return self.params.time_stop_bars  # type: ignore[attr-defined]
