"""Strategy selection. ``FixedSelector`` serves single-strategy runs; the evidence-based
``AdaptiveSelector`` lives in ``app.research.adaptive`` and implements the same interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from app.strategies.base import Strategy


@dataclass
class SelectorChoice:
    strategy: str | None
    reason: str
    eligible: bool
    eligibility_detail: str
    scores: dict[str, Any] = field(default_factory=dict)


class Selector(Protocol):
    def choose(self, pair: str, ts: int, regime_row: Mapping[str, Any]) -> SelectorChoice: ...


class FixedSelector:
    """Always the same strategy, but only inside the regimes it was designed for."""

    def __init__(self, strategy: Strategy, label: str = "single-strategy run") -> None:
        self.strategy = strategy
        self.label = label

    def choose(self, pair: str, ts: int, regime_row: Mapping[str, Any]) -> SelectorChoice:
        regime = str(regime_row["regime"])
        if regime == "uncertain":
            return SelectorChoice(None, f"no trade: regime uncertain ({regime_row['regime_reason']})", False,
                                  "regime uncertain")
        if regime not in self.strategy.required_regimes:
            return SelectorChoice(None, f"no trade: {self.strategy.name} does not trade in {regime}", False,
                                  f"{regime} is outside {list(self.strategy.required_regimes)}")
        return SelectorChoice(self.strategy.name, f"{self.label}: {self.strategy.name}", True, self.label)
