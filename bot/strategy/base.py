"""Strategy interface. A strategy sees closed candles only and returns a Signal."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from bot.models import Signal

REQUIRED_COLUMNS = ("ts", "open", "high", "low", "close", "volume")


class Strategy(ABC):
    """Base class. Subclasses must be pure: same candles in, same signal out."""

    name: str = "base"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.raw_params = dict(params or {})

    @property
    @abstractmethod
    def warmup(self) -> int:
        """Minimum number of closed candles needed before signals are meaningful."""

    @abstractmethod
    def on_candle(self, df: pd.DataFrame) -> Signal:
        """df: closed candles with columns ts, open, high, low, close, volume; last row is newest.

        Subclasses may accept an optional `context` keyword (symbol, timeframe, position,
        risk state); the engine passes it when the signature allows it."""

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": self.raw_params, "warmup": self.warmup}


def validate_frame(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"candle frame is missing columns: {missing}")
