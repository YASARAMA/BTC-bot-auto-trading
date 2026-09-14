"""Buy once and never sell: the benchmark every other strategy has to beat.

It exists so a backtest or a walk-forward run can be compared against the alternative that
costs nothing and takes no attention. A strategy that does not beat this on the same data
is not worth running, whatever its Sharpe ratio says.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from bot.models import Action, Signal
from bot.strategy.base import Strategy, validate_frame


class BuyHoldParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warmup: int = Field(2, ge=1, description="candles to wait before buying")
    stop_loss_pct: float = Field(0.0, ge=0.0, lt=100.0, description="optional protective stop, 0 = none")


class BuyHoldStrategy(Strategy):
    name = "buy_hold"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.p = BuyHoldParams.model_validate(self.raw_params)
        self.bought = False

    @property
    def warmup(self) -> int:
        return self.p.warmup

    def on_candle(self, df: pd.DataFrame) -> Signal:
        validate_frame(df)
        if len(df) < self.warmup:
            return Signal.hold(f"warming up ({len(df)}/{self.warmup} candles)")
        if self.bought:
            return Signal.hold("holding")
        self.bought = True
        close = float(df["close"].iloc[-1])
        stop = close * (1 - self.p.stop_loss_pct / 100.0) if self.p.stop_loss_pct else None
        return Signal(Action.BUY, 1.0, f"benchmark: bought once at {close:.2f} and holds", stop_loss=stop)
