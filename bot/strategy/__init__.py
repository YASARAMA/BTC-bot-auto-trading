"""Strategy registry. Add new strategies here to make them available by name."""
from __future__ import annotations

from typing import Any

from bot.strategy.base import Strategy
from bot.strategy.ema_rsi import EmaRsiStrategy

STRATEGIES: dict[str, type[Strategy]] = {
    EmaRsiStrategy.name: EmaRsiStrategy,
}


def get_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError as exc:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(STRATEGIES)}") from exc
    return cls(params)


__all__ = ["STRATEGIES", "Strategy", "get_strategy"]
