"""All strategies by name."""
from __future__ import annotations

from typing import Any

from app.core.errors import InvalidRequest
from app.strategies.base import Strategy
from app.strategies.mean_reversion import MeanReversion
from app.strategies.momentum_breakout import MomentumBreakout
from app.strategies.trend_following import TrendFollowing
from app.strategies.volatility_breakout import VolatilityBreakout

STRATEGIES: dict[str, type[Strategy]] = {
    TrendFollowing.name: TrendFollowing,
    MomentumBreakout.name: MomentumBreakout,
    MeanReversion.name: MeanReversion,
    VolatilityBreakout.name: VolatilityBreakout,
}


def build(name: str, params: dict[str, Any] | None = None) -> Strategy:
    cls = STRATEGIES.get(name)
    if cls is None:
        raise InvalidRequest(f"unknown strategy {name!r}; available: {', '.join(STRATEGIES)}")
    try:
        return cls(params)
    except ValueError as exc:
        raise InvalidRequest(f"invalid parameters for {name}: {exc}") from None


def names() -> list[str]:
    return list(STRATEGIES)
