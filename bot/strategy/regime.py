"""Regime switch: trend-follow when the market trends, fade extremes when it ranges.

Breakout and mean reversion fail in each other's conditions. This strategy measures how
strongly the market is trending (ADX) and hands the decision to whichever of the two suits
the current regime, so neither is asked to work where it cannot.

While a position is open the strategy that opened it keeps managing the exit, even if the
regime flips in the meantime: switching mid-trade would leave a position nobody owns. The
engine tells us on every candle whether a position really exists, so a stop-out, a manual
close or an entry the risk manager refused cannot leave ownership hanging.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bot.models import Action, Signal
from bot.strategy.base import Strategy, prune_extras, validate_frame
from bot.strategy.breakout import BreakoutParams, BreakoutStrategy
from bot.strategy.indicators import adx
from bot.strategy.mean_reversion import MeanReversionParams, MeanReversionStrategy

log = logging.getLogger("bot.strategy.regime")


class RegimeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adx_period: int = Field(14, ge=2)
    trend_above: float = Field(25.0, gt=0.0, description="ADX at or above this counts as a trend")
    range_below: float = Field(20.0, gt=0.0, description="ADX at or below this counts as a range")
    trend_params: dict[str, Any] = Field(default_factory=dict, description="breakout settings")
    range_params: dict[str, Any] = Field(default_factory=dict, description="mean reversion settings")

    @model_validator(mode="after")
    def _check(self) -> "RegimeParams":
        if self.range_below > self.trend_above:
            raise ValueError("range_below must not be above trend_above")
        return self


class RegimeStrategy(Strategy):
    name = "regime"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.p = RegimeParams.model_validate(self.raw_params)
        # Each half rejects the other half's parameter names, and a config that has been
        # switched between strategies often carries both.
        trend_params, dropped_trend = prune_extras(BreakoutParams, self.p.trend_params)
        range_params, dropped_range = prune_extras(MeanReversionParams, self.p.range_params)
        for group, names in (("trend_params", dropped_trend), ("range_params", dropped_range)):
            if names:
                log.warning("regime: ignoring unknown %s: %s", group, ", ".join(sorted(set(names))))
        self.trend = BreakoutStrategy(trend_params)
        self.range = MeanReversionStrategy(range_params)
        self.owner: str | None = None  # which sub-strategy opened the position we are in
        self._pre: pd.Series | None = None
        self._pre_index: dict[int, int] = {}

    @property
    def warmup(self) -> int:
        return max(self.trend.warmup, self.range.warmup, self.p.adx_period * 3) + 2

    def precompute(self, df: pd.DataFrame) -> None:
        self.trend.precompute(df)
        self.range.precompute(df)
        self._pre = adx(df, self.p.adx_period)
        self._pre_index = {int(ts): i for i, ts in enumerate(df["ts"].tolist())}

    def clear_precomputed(self) -> None:
        self.trend.clear_precomputed()
        self.range.clear_precomputed()
        self._pre, self._pre_index = None, {}

    def _adx(self, df: pd.DataFrame) -> float:
        if self._pre is not None and len(df):
            end = self._pre_index.get(int(df["ts"].iloc[-1]))
            if end is not None:
                return float(self._pre.iloc[end])
        return float(adx(df, self.p.adx_period).iloc[-1])

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Whatever the active half uses, plus the ADX that chose it (for the chart)."""
        active = self.range if self.owner == "range" else self.trend
        out = active.indicators(df).reset_index(drop=True)
        series = None
        if self._pre is not None and len(df):
            end = self._pre_index.get(int(df["ts"].iloc[-1]))
            if end is not None and end + 1 >= len(df):
                series = self._pre.iloc[end + 1 - len(df) : end + 1]
        if series is None:
            series = adx(df, self.p.adx_period)
        out["adx"] = series.reset_index(drop=True)
        return out

    def _sync_owner(self, context: dict[str, Any] | None) -> None:
        """Keep ownership in step with the position the engine actually holds.

        Without this, an entry the risk manager refused would leave us waiting forever for
        an exit on a position that was never opened, and a stop-out would do the same.
        """
        if not context or "position" not in context:
            return
        if context["position"] is None:
            self.owner = None

    def on_candle(self, df: pd.DataFrame, context: dict[str, Any] | None = None) -> Signal:
        validate_frame(df)
        self._sync_owner(context)
        if len(df) < self.warmup:
            return Signal.hold(f"warming up ({len(df)}/{self.warmup} candles)")
        strength = self._adx(df)
        if pd.isna(strength):
            return Signal.hold("ADX not ready")

        # In a position, the half that opened it decides when to leave.
        if self.owner:
            owner = self.owner
            active = self.trend if owner == "trend" else self.range
            signal = active.on_candle(df)
            if signal.action == Action.SELL:
                self.owner = None
                return Signal(Action.SELL, signal.confidence, f"[{owner}] {signal.reason}")
            if signal.action == Action.BUY:  # already long: nothing to add
                return Signal.hold(f"[{owner}] holding the open position; adx={strength:.1f}")
            return Signal.hold(f"[{owner}] {signal.reason}; adx={strength:.1f}")

        if strength >= self.p.trend_above:
            regime, active = "trend", self.trend
        elif strength <= self.p.range_below:
            regime, active = "range", self.range
        else:
            return Signal.hold(f"adx={strength:.1f} is between {self.p.range_below:g} and "
                               f"{self.p.trend_above:g}: neither a clear trend nor a clear range")

        signal = active.on_candle(df)
        if signal.action == Action.BUY:
            self.owner = regime
            return Signal(Action.BUY, signal.confidence, f"[{regime}] {signal.reason}; adx={strength:.1f}",
                          stop_loss=signal.stop_loss, take_profit=signal.take_profit, atr=signal.atr)
        if signal.action == Action.SELL:
            return Signal.hold(f"[{regime}] exit signal with no position; adx={strength:.1f}")
        return Signal.hold(f"[{regime}] {signal.reason}; adx={strength:.1f}")

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "regime_owner": self.owner}
