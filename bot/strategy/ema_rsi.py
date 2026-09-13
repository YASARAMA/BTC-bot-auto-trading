"""Reference strategy: EMA crossover with an RSI filter and ATR-based exits.

BUY  when the fast EMA crosses above the slow EMA on the closed candle and RSI is
     inside [rsi_buy_min, rsi_buy_max] (momentum present, not overbought).
SELL when the fast EMA crosses below the slow EMA.
Stop loss and take profit are placed at multiples of ATR from the close.

Confidence starts at confidence_base and gains confidence_per_confirmation for each
of: slow EMA rising over trend_lookback candles, RSI above rsi_midline. Values are
params so nothing is hardcoded in the decision logic.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bot.models import Action, Signal
from bot.strategy.base import Strategy, validate_frame
from bot.strategy.indicators import atr, ema, rsi


class EmaRsiParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ema_fast: int = Field(12, ge=1)
    ema_slow: int = Field(26, ge=2)
    rsi_period: int = Field(14, ge=2)
    rsi_buy_min: float = Field(45.0, ge=0.0, le=100.0)
    rsi_buy_max: float = Field(70.0, ge=0.0, le=100.0)
    rsi_midline: float = Field(50.0, ge=0.0, le=100.0)
    atr_period: int = Field(14, ge=1)
    atr_stop_mult: float = Field(2.0, gt=0.0)
    atr_tp_mult: float = Field(3.0, gt=0.0)
    trend_lookback: int = Field(5, ge=1)
    trend_filter_period: int = Field(0, ge=0, description="only buy above this EMA; 0 turns the filter off")
    confidence_base: float = Field(0.5, ge=0.0, le=1.0)
    confidence_per_confirmation: float = Field(0.25, ge=0.0, le=1.0)
    warmup_factor: int = Field(3, ge=1, description="warmup = longest period * factor")

    @model_validator(mode="after")
    def _check(self) -> "EmaRsiParams":
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be shorter than ema_slow")
        if self.rsi_buy_min >= self.rsi_buy_max:
            raise ValueError("rsi_buy_min must be below rsi_buy_max")
        return self


class EmaRsiStrategy(Strategy):
    name = "ema_rsi"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.p = EmaRsiParams.model_validate(self.raw_params)
        self._pre: pd.DataFrame | None = None
        self._pre_index: dict[int, int] = {}

    @property
    def warmup(self) -> int:
        # Signal periods get the full factor because their exact value decides the entry.
        # The trend line only decides "above or below", so a shorter seed is enough; a long
        # filter would otherwise need more history than the bot keeps in memory.
        signal = max(self.p.ema_slow, self.p.rsi_period, self.p.atr_period) * self.p.warmup_factor
        trend = int(self.p.trend_filter_period * 1.25) + 20 if self.p.trend_filter_period else 0
        return max(signal, trend) + self.p.trend_lookback + 1

    def precompute(self, df: pd.DataFrame) -> None:
        """Compute the indicators once for a whole series (used by the backtester)."""
        self._pre = self._compute(df)
        self._pre_index = {int(ts): i for i, ts in enumerate(df["ts"].tolist())}

    def clear_precomputed(self) -> None:
        self._pre, self._pre_index = None, {}

    def _compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        out["ema_fast"] = ema(df["close"], self.p.ema_fast)
        out["ema_slow"] = ema(df["close"], self.p.ema_slow)
        out["rsi"] = rsi(df["close"], self.p.rsi_period)
        out["atr"] = atr(df, self.p.atr_period)
        if self.p.trend_filter_period:
            out["ema_trend"] = ema(df["close"], self.p.trend_filter_period)
        return out

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if self._pre is not None and len(df):
            end = self._pre_index.get(int(df["ts"].iloc[-1]))
            if end is not None and end + 1 >= len(df):
                # The last two rows are all on_candle() reads; slicing keeps it cheap.
                return self._pre.iloc[end + 1 - len(df) : end + 1].reset_index(drop=True)
        return self._compute(df)

    def on_candle(self, df: pd.DataFrame) -> Signal:
        validate_frame(df)
        if len(df) < self.warmup:
            return Signal.hold(f"warming up ({len(df)}/{self.warmup} candles)")

        ind = self.indicators(df)
        last, prev = ind.iloc[-1], ind.iloc[-2]
        close = float(df["close"].iloc[-1])
        values = {
            "close": close,
            "ema_fast": float(last["ema_fast"]),
            "ema_slow": float(last["ema_slow"]),
            "rsi": float(last["rsi"]),
            "atr": float(last["atr"]),
        }
        if any(pd.isna(v) for v in values.values()) or pd.isna(prev["ema_fast"]) or pd.isna(prev["ema_slow"]):
            return Signal.hold("indicators not ready (NaN)")
        if values["atr"] <= 0.0:
            return Signal.hold("ATR is zero; no volatility to size a stop")

        cross_up = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
        cross_down = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]
        detail = (
            f"ema_fast={values['ema_fast']:.2f} ema_slow={values['ema_slow']:.2f} "
            f"rsi={values['rsi']:.1f} atr={values['atr']:.2f} close={close:.2f}"
        )

        if cross_up:
            if self.p.trend_filter_period:
                trend = float(last.get("ema_trend", float("nan")))
                if pd.isna(trend):
                    return Signal.hold(f"trend filter not ready; {detail}")
                if close <= trend:
                    return Signal.hold(
                        f"EMA cross up but price {close:.2f} is below the "
                        f"{self.p.trend_filter_period}-period trend line {trend:.2f}; {detail}")
            if not (self.p.rsi_buy_min <= values["rsi"] <= self.p.rsi_buy_max):
                return Signal.hold(
                    f"EMA cross up but RSI {values['rsi']:.1f} outside "
                    f"[{self.p.rsi_buy_min}, {self.p.rsi_buy_max}]; {detail}"
                )
            slow_then = float(ind["ema_slow"].iloc[-1 - self.p.trend_lookback])
            confirmations = 0
            notes = []
            if values["ema_slow"] > slow_then:
                confirmations += 1
                notes.append("slow EMA rising")
            if values["rsi"] > self.p.rsi_midline:
                confirmations += 1
                notes.append(f"RSI above {self.p.rsi_midline:g}")
            confidence = min(1.0, self.p.confidence_base + confirmations * self.p.confidence_per_confirmation)
            stop = close - self.p.atr_stop_mult * values["atr"]
            take = close + self.p.atr_tp_mult * values["atr"]
            if stop <= 0.0:
                return Signal.hold(f"computed stop {stop:.2f} is not positive; {detail}")
            why = "EMA cross up with RSI in range" + (f" ({', '.join(notes)})" if notes else "")
            return Signal(Action.BUY, confidence, f"{why}; {detail}", stop_loss=stop, take_profit=take)

        if cross_down:
            return Signal(Action.SELL, 1.0, f"EMA cross down; {detail}")

        return Signal.hold(f"no crossover; {detail}")
