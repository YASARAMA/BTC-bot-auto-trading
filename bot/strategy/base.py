"""Strategy interface. A strategy sees closed candles only and returns a Signal."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd
from pydantic import BaseModel, ValidationError

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

    def precompute(self, df: pd.DataFrame) -> None:
        """Optional: compute indicators once over a whole historical series.

        The backtester calls this before replaying the candles; the live bot never does.
        Indicators here converge long before the warmup ends, so the values are the same
        either way (tests/test_strategy.py checks that they agree to 1e-6).
        """

    def clear_precomputed(self) -> None:
        """Forget anything precompute() cached."""


_MISSING = object()


def drop_path(params: dict[str, Any], path: tuple[Any, ...]) -> bool:
    """Remove one parameter addressed by a pydantic error location. True if it was there."""
    node: Any = params
    for key in path[:-1]:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return isinstance(node, dict) and node.pop(path[-1], _MISSING) is not _MISSING


def prune_extras(model: type[BaseModel], params: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Drop the settings this parameter model does not recognise, and name them.

    Switching strategy leaves the previous one\'s parameters behind in config.yaml, and
    every parameter model rejects names it does not know. Dropping them beats refusing to
    start. Errors that are not about unknown names (a period of -5, a string where a
    number belongs) are left untouched, so a genuinely broken setting still raises.
    """
    clean = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (params or {}).items()}
    dropped: list[str] = []
    for _ in range(5):  # each pass removes every unknown name pydantic reported
        try:
            model.model_validate(clean)
            break
        except ValidationError as exc:
            locs = [tuple(e["loc"]) for e in exc.errors()
                    if e.get("type") == "extra_forbidden" and e.get("loc")]
            removed = [loc for loc in locs if drop_path(clean, loc)]
            if not removed:
                break
            dropped.extend(".".join(str(x) for x in loc) for loc in removed)
    return clean, dropped


def validate_frame(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"candle frame is missing columns: {missing}")
