"""Bollinger mean reversion: buy a stretched-down price, sell when it comes back.

The mirror image of breakout trading, and it fails in exactly the opposite conditions: it
earns steadily in a range and loses badly in a sustained downtrend, which is why the trend
filter here refuses to buy unless the long moving average is still rising. (Filtering on
price above that average instead would block every entry: a dip is below the average by
definition.)
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bot.models import Action, Signal
from bot.strategy.base import Strategy, validate_frame
from bot.strategy.indicators import atr, bollinger, ema, rsi


class MeanReversionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bb_period: int = Field(20, ge=5)
    bb_std: float = Field(2.0, gt=0.0)
    rsi_period: int = Field(14, ge=2)
    rsi_buy_max: float = Field(35.0, ge=1.0, le=99.0, description="only buy when RSI is below this")
    exit_band: str = Field("middle", description="middle or upper: where the trade is closed")
    atr_period: int = Field(14, ge=1)
    atr_stop_mult: float = Field(2.5, gt=0.0)
    trend_filter_period: int = Field(200, ge=0, description="require this EMA to be rising; 0 turns the filter off")
    trend_slope_lookback: int = Field(20, ge=1, description="over how many candles the trend EMA must be rising")
    max_stretch_atr: float = Field(0.0, ge=0.0, description="skip entries further than this below the band; 0 = off")
    confidence_base: float = Field(0.5, ge=0.0, le=1.0)
    confidence_per_confirmation: float = Field(0.25, ge=0.0, le=1.0)
    warmup_factor: int = Field(3, ge=1)

    @model_validator(mode="after")
    def _check(self) -> "MeanReversionParams":
        if self.exit_band not in ("middle", "upper"):
            raise ValueError("exit_band must be 'middle' or 'upper'")
        return self


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.p = MeanReversionParams.model_validate(self.raw_params)
        self._pre: pd.DataFrame | None = None
        self._pre_index: dict[int, int] = {}

    @property
    def warmup(self) -> int:
        base = max(self.p.bb_period, self.p.rsi_period, self.p.atr_period) * self.p.warmup_factor
        trend = (int(self.p.trend_filter_period * 1.25) + self.p.trend_slope_lookback + 20
                 if self.p.trend_filter_period else 0)
        return max(base, trend) + 2

    def precompute(self, df: pd.DataFrame) -> None:
        self._pre = self._compute(df)
        self._pre_index = {int(ts): i for i, ts in enumerate(df["ts"].tolist())}

    def clear_precomputed(self) -> None:
        self._pre, self._pre_index = None, {}

    def _compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        mid, upper, lower = bollinger(df["close"], self.p.bb_period, self.p.bb_std)
        out["bb_mid"], out["bb_upper"], out["bb_lower"] = mid, upper, lower
        out["rsi"] = rsi(df["close"], self.p.rsi_period)
        out["atr"] = atr(df, self.p.atr_period)
        if self.p.trend_filter_period:
            out["ema_trend"] = ema(df["close"], self.p.trend_filter_period)
        return out

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if self._pre is not None and len(df):
            end = self._pre_index.get(int(df["ts"].iloc[-1]))
            if end is not None and end + 1 >= len(df):
                return self._pre.iloc[end + 1 - len(df) : end + 1].reset_index(drop=True)
        return self._compute(df)

    def on_candle(self, df: pd.DataFrame) -> Signal:
        validate_frame(df)
        if len(df) < self.warmup:
            return Signal.hold(f"warming up ({len(df)}/{self.warmup} candles)")
        ind = self.indicators(df)
        last = ind.iloc[-1]
        close = float(df["close"].iloc[-1])
        mid, upper, lower = float(last["bb_mid"]), float(last["bb_upper"]), float(last["bb_lower"])
        rsi_now, atr_now = float(last["rsi"]), float(last["atr"])
        if any(pd.isna(v) for v in (mid, upper, lower, rsi_now, atr_now)):
            return Signal.hold("bands not ready (NaN)")
        if atr_now <= 0:
            return Signal.hold("ATR is zero; no volatility to size a stop")
        detail = f"close={close:.2f} band={lower:.2f}/{mid:.2f}/{upper:.2f} rsi={rsi_now:.1f} atr={atr_now:.2f}"

        exit_level = mid if self.p.exit_band == "middle" else upper
        if close >= exit_level:
            return Signal(Action.SELL, 1.0, f"back at the {self.p.exit_band} band; {detail}")

        if close <= lower:
            if rsi_now > self.p.rsi_buy_max:
                return Signal.hold(f"below the band but RSI {rsi_now:.1f} is above {self.p.rsi_buy_max:g}; {detail}")
            stretch = lower - close
            if self.p.max_stretch_atr and stretch > self.p.max_stretch_atr * atr_now:
                return Signal.hold(
                    f"price is {stretch / atr_now:.1f} ATR below the band, too far to call it a bounce; {detail}")
            if self.p.trend_filter_period:
                # A dip is by definition below the average, so "price above the trend line"
                # would block every entry. What matters is the direction the trend line is
                # moving: buying dips only makes sense while the longer trend is still up.
                trend = float(last.get("ema_trend", float("nan")))
                back = len(ind) - 1 - self.p.trend_slope_lookback
                earlier = float(ind["ema_trend"].iloc[back]) if back >= 0 else float("nan")
                if pd.isna(trend) or pd.isna(earlier):
                    return Signal.hold(f"trend filter not ready; {detail}")
                if trend <= earlier:
                    return Signal.hold(
                        f"the {self.p.trend_filter_period}-period trend is falling, so this is a downtrend "
                        f"rather than a dip; {detail}")
            confirmations = 0
            notes = []
            if rsi_now < self.p.rsi_buy_max * 0.75:
                confirmations += 1
                notes.append("RSI deeply oversold")
            if close > float(df["low"].iloc[-1]):
                confirmations += 1
                notes.append("closed above the candle low")
            confidence = min(1.0, self.p.confidence_base + confirmations * self.p.confidence_per_confirmation)
            stop = close - self.p.atr_stop_mult * atr_now
            if stop <= 0:
                return Signal.hold(f"computed stop {stop:.2f} is not positive; {detail}")
            why = "closed below the lower band with RSI oversold" + (f" ({', '.join(notes)})" if notes else "")
            return Signal(Action.BUY, confidence, f"{why}; {detail}", stop_loss=stop, take_profit=exit_level,
                          atr=atr_now)

        return Signal.hold(f"inside the bands; {detail}")
