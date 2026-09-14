"""Strategy registry. Add new strategies here to make them available by name."""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from bot.strategy.base import Strategy, drop_path
from bot.strategy.ai import AiStrategy
from bot.strategy.breakout import BreakoutStrategy
from bot.strategy.buy_hold import BuyHoldStrategy
from bot.strategy.ema_rsi import EmaRsiStrategy
from bot.strategy.mean_reversion import MeanReversionStrategy
from bot.strategy.regime import RegimeStrategy

log = logging.getLogger("bot.strategy")

STRATEGIES: dict[str, type[Strategy]] = {
    EmaRsiStrategy.name: EmaRsiStrategy,
    BreakoutStrategy.name: BreakoutStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    RegimeStrategy.name: RegimeStrategy,
    BuyHoldStrategy.name: BuyHoldStrategy,
    AiStrategy.name: AiStrategy,
}


def prune_params(name: str, params: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """The parameters a strategy can actually use, plus the names that were dropped.

    See `prune_extras` in bot/strategy/base.py for why unknown names are dropped rather
    than treated as a fatal configuration error.
    """
    cls = STRATEGIES.get(name)
    clean = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (params or {}).items()}
    if cls is None:
        return clean, []
    dropped: list[str] = []
    for _ in range(5):
        try:
            cls(clean)
            break
        except ValidationError as exc:
            locs = [tuple(e["loc"]) for e in exc.errors()
                    if e.get("type") == "extra_forbidden" and e.get("loc")]
            removed = [loc for loc in locs if drop_path(clean, loc)]
            if not removed:
                break
            dropped.extend(".".join(str(x) for x in loc) for loc in removed)
        except Exception:  # noqa: BLE001 - a different problem, not ours to paper over
            break
    return clean, dropped


def get_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError as exc:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(STRATEGIES)}") from exc
    clean, dropped = prune_params(name, params)
    if dropped:
        log.warning("strategy %s: ignoring %d setting(s) that belong to another strategy: %s",
                    name, len(dropped), ", ".join(sorted(set(dropped))))
    return cls(clean)


__all__ = ["STRATEGIES", "Strategy", "get_strategy", "prune_params"]
