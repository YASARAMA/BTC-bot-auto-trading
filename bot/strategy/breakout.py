"""Donchian breakout: buy when price clears the recent high, leave when it loses the recent low.

The oldest trend-following idea there is. It gives up a lot of small losses in a range and
pays for them with the occasional long move, so it only makes sense with an exit that lets
a winner run (see the trailing stop under `exits` in the configuration).
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bot.models import Action, Signal
from bot.strategy.base import Strategy, validate_frame
from bot.strategy.indicators import atr, donchian, ema


class BreakoutParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_period: int = Field(20, ge=2, description="buy above the highest high of this many candles")
    exit_period: int = Field(10, ge=2, description="sell below the lowest low of this many candles")
    atr_period: int = Field(14, ge=1)
    atr_stop_mult: float = Field(2.0, gt=0.0)
    atr_tp_mult: float = Field(0.0, ge=0.0, description="0 means no fixed target: the channel exit closes it")
    trend_filter_period: int = Field(0, ge=0, description="only buy above this EMA; 0 turns the filter off")
    min_breakout_atr: float = Field(0.0, ge=0.0, description="require the break to clear the channel by this much ATR")
    confirm_candles: int = Field(
        1, ge=1, le=10, description="how many consecutive closes must hold above the channel before buying")
    close_strength_min: float = Field(
        0.0, ge=0.0, le=1.0,
        description="where in its own range the breakout candle must close (0.8 = in the top fifth); 0 = off")
    volume_min_ratio: float = Field(
        0.0, ge=0.0, description="require volume at least this many times the channel average; 0 = off")
    stop_at_channel: bool = Field(
        False, description="put the stop under the exit channel (structure) instead of a fixed ATR distance")
    stop_channel_buffer_atr: float = Field(
        0.25, ge=0.0, description="how far under that channel low the structural stop sits")
    confidence_base: float = Field(0.55, ge=0.0, le=1.0)
    confidence_per_confirmation: float = Field(0.2, ge=0.0, le=1.0)
    warmup_factor: int = Field(2, ge=1)

    @model_validator(mode="after")
    def _check(self) -> "BreakoutParams":
        if self.exit_period > self.entry_period:
            raise ValueError("exit_period should not be longer than entry_period, or the exit never triggers")
        return self


class BreakoutStrategy(Strategy):
    name = "breakout"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.p = BreakoutParams.model_validate(self.raw_params)
        self._pre: pd.DataFrame | None = None
        self._pre_index: dict[int, int] = {}

    @property
    def warmup(self) -> int:
        base = max(self.p.entry_period, self.p.exit_period, self.p.atr_period) * self.p.warmup_factor
        trend = int(self.p.trend_filter_period * 1.25) + 20 if self.p.trend_filter_period else 0
        return max(base, trend) + 2

    def precompute(self, df: pd.DataFrame) -> None:
        self._pre = self._compute(df)
        self._pre_index = {int(ts): i for i, ts in enumerate(df["ts"].tolist())}

    def clear_precomputed(self) -> None:
        self._pre, self._pre_index = None, {}

    def _compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        out["channel_high"], _ = donchian(df, self.p.entry_period)
        _, out["channel_low"] = donchian(df, self.p.exit_period)
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
        high, low, atr_now = (float(last["channel_high"]), float(last["channel_low"]), float(last["atr"]))
        if any(pd.isna(v) for v in (high, low, atr_now)):
            return Signal.hold("channel not ready (NaN)")
        if atr_now <= 0:
            return Signal.hold("ATR is zero; no volatility to size a stop")
        detail = (f"close={close:.2f} channel={low:.2f}-{high:.2f} atr={atr_now:.2f} "
                  f"({self.p.entry_period}/{self.p.exit_period})")

        if close < low:
            return Signal(Action.SELL, 1.0, f"price lost the {self.p.exit_period}-candle low; {detail}")

        margin = close - high
        if margin > 0:
            if self.p.min_breakout_atr and margin < self.p.min_breakout_atr * atr_now:
                return Signal.hold(
                    f"break of {margin:.2f} is smaller than {self.p.min_breakout_atr:g} ATR; {detail}")
            if self.p.trend_filter_period:
                trend = float(last.get("ema_trend", float("nan")))
                if pd.isna(trend):
                    return Signal.hold(f"trend filter not ready; {detail}")
                if close <= trend:
                    return Signal.hold(f"breakout below the {self.p.trend_filter_period}-period trend line; {detail}")

            # A breakout that closes at the bottom of its own candle was sold into: the
            # buyers were there and then they were not.
            candle_high, candle_low = float(df["high"].iloc[-1]), float(df["low"].iloc[-1])
            span = candle_high - candle_low
            strength = (close - candle_low) / span if span > 0 else 1.0
            if self.p.close_strength_min and strength < self.p.close_strength_min:
                return Signal.hold(
                    f"closed {strength:.0%} up its own candle, below the {self.p.close_strength_min:.0%} "
                    f"required for a break worth buying; {detail}")

            # Not every data source carries volume: some CSV exports have no column for it,
            # and the loader fills zeros. A filter with nothing to measure has to stand
            # aside, or it would silently refuse every entry for the life of the file.
            channel_volume = float(df["volume"].iloc[-self.p.entry_period:].mean())
            volume_known = channel_volume > 0
            volume_ratio = float(df["volume"].iloc[-1]) / channel_volume if volume_known else 0.0
            if self.p.volume_min_ratio and volume_known and volume_ratio < self.p.volume_min_ratio:
                return Signal.hold(
                    f"volume {volume_ratio:.2f}x the channel average, below the {self.p.volume_min_ratio:g}x "
                    f"a real break usually brings; {detail}")

            # A single close above the channel can be a wick that the next candle takes back.
            if self.p.confirm_candles > 1:
                needed = self.p.confirm_candles
                closes = df["close"].iloc[-needed:]
                levels = ind["channel_high"].iloc[-needed:]
                if len(closes) < needed or not bool((closes.to_numpy() > levels.to_numpy()).all()):
                    held = int((closes.to_numpy() > levels.to_numpy()).sum())
                    return Signal.hold(f"held above the channel for {held} of {needed} candles; {detail}")

            confirmations = 0
            notes = []
            if margin > atr_now:
                confirmations += 1
                notes.append("break larger than one ATR")
            if volume_known and volume_ratio > 1.0:
                confirmations += 1
                notes.append("volume above its channel average")
            confidence = min(1.0, self.p.confidence_base + confirmations * self.p.confidence_per_confirmation)
            stop = close - self.p.atr_stop_mult * atr_now
            if self.p.stop_at_channel:
                # The level the trade is wrong at is the channel it broke out of, not a
                # distance in volatility units. Never wider than the ATR stop.
                structural = low - self.p.stop_channel_buffer_atr * atr_now
                stop = max(stop, structural) if structural < close else stop
            if stop <= 0 or stop >= close:
                return Signal.hold(f"computed stop {stop:.2f} is not usable; {detail}")
            take = close + self.p.atr_tp_mult * atr_now if self.p.atr_tp_mult else None
            why = f"broke the {self.p.entry_period}-candle high" + (f" ({', '.join(notes)})" if notes else "")
            return Signal(Action.BUY, confidence, f"{why}; {detail}", stop_loss=stop, take_profit=take,
                          atr=atr_now)

        return Signal.hold(f"inside the channel; {detail}")
