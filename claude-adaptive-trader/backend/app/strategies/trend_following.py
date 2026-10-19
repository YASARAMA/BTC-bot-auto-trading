"""Strategy A - trend following: buy a completed pullback inside a confirmed uptrend."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from app.regimes.detector import TREND_DOWN, TREND_UP
from app.strategies.base import (SignalFrame, Strategy, StrategyContext, StrategyParams, cost_ok, htf_all_up)


class TrendParams(StrategyParams):
    pullback_lookback: int = Field(6, ge=2, le=50, description="candles in which the pullback must have happened")
    pullback_tolerance_atr: float = Field(0.3, ge=0, le=3, description="how close to the fast EMA counts as a touch")
    rsi_pullback_max: float = Field(50.0, ge=10, le=70, description="RSI must have dipped to this during the pullback")
    max_extension_atr: float = Field(1.5, gt=0, le=6, description="no entry further than this above the fast EMA")
    stop_lookback: int = Field(10, ge=2, le=100)
    stop_buffer_atr: float = Field(0.3, ge=0, le=3)
    min_stop_atr: float = Field(1.0, gt=0, le=5)
    max_stop_atr: float = Field(3.0, gt=0, le=10)
    target_r: float = Field(2.0, gt=0, le=10, description="take profit at this multiple of the initial risk")
    time_stop_bars: int = Field(96, ge=1, le=2000)
    require_all_htf_up: bool = True
    min_reward_cost_ratio: float = Field(3.0, ge=0, le=50)


class TrendFollowing(Strategy):
    name = "trend_following"
    title = "A · Trend following (pullback)"
    code_version = "1.0"
    hypothesis = ("When the 1h and 4h trends point up, a pullback to the fast EMA that is followed by a completed "
                  "candle closing above the previous high tends to resume the trend often enough to pay for "
                  "its costs and losers.")
    required_regimes = (TREND_UP,)
    entry_rules = (
        "regime is trend_up",
        "every confirmation timeframe (1h, 4h) is in an uptrend",
        "price touched the fast EMA zone within the pullback window",
        "RSI dipped to the pullback threshold within the window",
        "the pullback stayed above the slow EMA and price is back above the mid EMA",
        "completed candle closes above the previous high, above the fast EMA, and is bullish",
        "close is not over-extended above the fast EMA",
        "stop distance is between the minimum and maximum ATR multiples",
        "the distance to the target pays the round-trip costs several times over",
    )
    exit_rules = ("close below the mid EMA (trend broken)", "regime turns trend_down",
                  "time stop", "hard stop or take profit")
    stop_rule = "below the lowest low of the stop lookback minus a buffer; at least min_stop_atr ATR away"
    target_rule = "entry + target_r x initial risk"
    Params = TrendParams
    tunable = ("pullback_lookback", "rsi_pullback_max", "max_extension_atr", "target_r", "time_stop_bars")

    def compute(self, f: pd.DataFrame, regime: pd.DataFrame, ctx: StrategyContext) -> SignalFrame:
        p: TrendParams = self.params  # type: ignore[assignment]
        close, low, high, atr = f["close"], f["low"], f["high"], f["atr"]
        L = p.pullback_lookback
        touched = ((low - f["ema_fast"]) <= p.pullback_tolerance_atr * atr).astype(float)
        touched_recent = touched.shift(1).rolling(L, min_periods=L).max() > 0
        rsi_dip = f["rsi"].shift(1).rolling(L, min_periods=L).min() <= p.rsi_pullback_max
        pullback_low = low.shift(1).rolling(L, min_periods=L).min()
        intact = (pullback_low > f["ema_slow"]) & (close > f["ema_mid"])
        confirm = (close > high.shift(1)) & (close > f["ema_fast"]) & (close > f["open"])
        not_extended = f["extension_atr"] <= p.max_extension_atr

        swing_low = low.rolling(p.stop_lookback, min_periods=p.stop_lookback).min()
        raw_stop = swing_low - p.stop_buffer_atr * atr
        min_stop = close - p.min_stop_atr * atr
        stop = np.minimum(raw_stop, min_stop)          # never closer than min_stop_atr
        stop_ok = (close - stop) <= p.max_stop_atr * atr
        target = close + p.target_r * (close - stop)
        pays = cost_ok(close, target, ctx, p.min_reward_cost_ratio)

        htf = htf_all_up(f, ctx.confirm_timeframes) if p.require_all_htf_up else pd.Series(True, index=f.index)
        conditions = {
            self.entry_rules[0]: regime["regime"] == TREND_UP,
            self.entry_rules[1]: htf,
            self.entry_rules[2]: touched_recent,
            self.entry_rules[3]: rsi_dip,
            self.entry_rules[4]: intact,
            self.entry_rules[5]: confirm,
            self.entry_rules[6]: not_extended,
            self.entry_rules[7]: stop_ok,
            self.entry_rules[8]: pays,
        }
        entry = pd.concat(conditions.values(), axis=1).fillna(False).all(axis=1)
        ext = f["extension_atr"].clip(lower=0) / p.max_extension_atr
        confidence = 0.5 + 0.3 * regime["regime_confidence"].fillna(0) + 0.2 * (1 - ext.clip(0, 1))
        broken = close < f["ema_mid"]
        reversed_ = regime["regime"] == TREND_DOWN
        exit_signal = broken | reversed_
        exit_reason = pd.Series(np.where(broken, "close below the mid EMA: trend broken",
                                         np.where(reversed_, "regime turned trend_down", "")), index=f.index)
        return SignalFrame(entry=entry, stop=pd.Series(stop, index=f.index), target=target,
                           invalidation=pd.Series(np.nan, index=f.index), confidence=confidence,
                           exit_signal=exit_signal, exit_reason=exit_reason, conditions=conditions)

    def time_stop_bars(self) -> int:
        return self.params.time_stop_bars  # type: ignore[attr-defined]
