"""The common strategy interface.

A strategy is a set of named, explicit entry conditions evaluated on the causal feature
frame, plus rules for the stop, the optional target, an optional close-based invalidation
level, state-based exits and a time stop. Everything is vectorised over the whole frame,
and every column in row ``t`` depends on rows ``<= t`` only, so the very same code produces
backtest signals and live paper-trading signals.

Each strategy exposes, as the brief requires: the data it needs, its entry and exit
conditions, its stop-loss logic, its optional take-profit, the signal timestamp and
direction (always long: short selling does not exist here), the invalidation level, the
regimes it is allowed to trade in, a human-readable reason for every signal, and a version
that changes whenever its code or parameters change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.core.hashing import stable_hash


class StrategyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class StrategyContext:
    """What a strategy may know besides market data: the cost assumptions it must beat."""

    round_trip_cost_pct: float
    signal_timeframe: str = "15m"
    confirm_timeframes: tuple[str, ...] = ("1h", "4h")


@dataclass
class SignalFrame:
    """Vectorised output of a strategy over a feature frame."""

    entry: pd.Series                 # bool: all entry conditions true on this closed candle
    stop: pd.Series                  # hard stop price (sell stop)
    target: pd.Series                # take-profit price or NaN
    invalidation: pd.Series          # close below this exits at the next open, or NaN
    confidence: pd.Series            # 0..1
    exit_signal: pd.Series           # bool: state-based exit for an open position
    exit_reason: pd.Series           # str
    conditions: dict[str, pd.Series] = field(default_factory=dict)   # named entry conditions


class Strategy(ABC):
    name: ClassVar[str]
    title: ClassVar[str]
    code_version: ClassVar[str]          # bump when the logic changes
    hypothesis: ClassVar[str]
    required_regimes: ClassVar[tuple[str, ...]]
    entry_rules: ClassVar[tuple[str, ...]]
    exit_rules: ClassVar[tuple[str, ...]]
    stop_rule: ClassVar[str]
    target_rule: ClassVar[str]
    Params: ClassVar[type[StrategyParams]]
    tunable: ClassVar[tuple[str, ...]] = ()   # parameters a sensitivity study may vary

    def __init__(self, params: dict[str, Any] | StrategyParams | None = None) -> None:
        if isinstance(params, StrategyParams):
            self.params = params
        else:
            self.params = self.Params.model_validate(params or {})

    # ----- identity --------------------------------------------------------------------
    @property
    def params_dict(self) -> dict[str, Any]:
        return self.params.model_dump(mode="json")

    @property
    def params_hash(self) -> str:
        return stable_hash({"strategy": self.name, "code": self.code_version, "params": self.params_dict}, 10)

    @property
    def version(self) -> str:
        return f"{self.name}@{self.code_version}+{self.params_hash}"

    @property
    def complexity(self) -> int:
        return len(self.tunable)

    def warmup_bars(self, rank_window: int, ema_slow: int) -> int:
        return max(rank_window, 3 * ema_slow) + 20

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name, "title": self.title, "code_version": self.code_version, "version": self.version,
            "hypothesis": self.hypothesis, "direction": "long only (spot)",
            "required_regimes": list(self.required_regimes), "entry_rules": list(self.entry_rules),
            "exit_rules": list(self.exit_rules), "stop_rule": self.stop_rule, "target_rule": self.target_rule,
            "params": self.params_dict, "tunable": list(self.tunable), "complexity": self.complexity,
            "params_schema": self.Params.model_json_schema(),
            "required_data": "closed candles on the signal timeframe; higher timeframes are built from them",
        }

    # ----- signals ------------------------------------------------------------------------
    @abstractmethod
    def compute(self, f: pd.DataFrame, regime: pd.DataFrame, ctx: StrategyContext) -> SignalFrame:
        """Vectorised signals. ``f`` is the feature frame, ``regime`` the detector output."""

    def signals(self, f: pd.DataFrame, regime: pd.DataFrame, ctx: StrategyContext) -> pd.DataFrame:
        sf = self.compute(f, regime, ctx)
        entry = sf.entry.fillna(False).astype(bool)
        valid_levels = (sf.stop > 0) & (sf.stop < f["close"]) & np.isfinite(sf.stop)
        target_ok = sf.target.isna() | (sf.target > f["close"])
        entry = entry & valid_levels & target_ok
        out = pd.DataFrame({
            "ts": f["ts"].astype("int64"),
            "entry": entry,
            "stop": sf.stop.where(entry),
            "target": sf.target.where(entry),
            "invalidation": sf.invalidation.where(entry),
            "confidence": sf.confidence.where(entry).clip(0, 1),
            "exit_signal": sf.exit_signal.fillna(False).astype(bool),
            "exit_reason": sf.exit_reason.where(sf.exit_signal.fillna(False).astype(bool), ""),
        })
        return out

    def explain(self, f: pd.DataFrame, regime: pd.DataFrame, ctx: StrategyContext, i: int) -> dict[str, Any]:
        """Which entry conditions held on row ``i``: the audit trail for a signal or a non-signal."""
        sf = self.compute(f, regime, ctx)
        checks = []
        for name, series in sf.conditions.items():
            value = series.iloc[i]
            checks.append({"condition": name, "passed": bool(value) if not pd.isna(value) else False})
        row = {
            "stop": _num(sf.stop.iloc[i]), "target": _num(sf.target.iloc[i]),
            "invalidation": _num(sf.invalidation.iloc[i]), "confidence": _num(sf.confidence.iloc[i]),
        }
        passed = all(c["passed"] for c in checks)
        return {"strategy": self.name, "version": self.version, "entry": passed, "checks": checks, "levels": row}

    def entry_reason(self, f: pd.DataFrame, regime: pd.DataFrame, i: int, sig: pd.Series) -> str:
        close = float(f["close"].iloc[i])
        stop = float(sig["stop"])
        risk_pct = (close - stop) / close * 100
        target = sig.get("target")
        tgt = "" if target is None or pd.isna(target) else f", target {float(target):.6g}"
        return (f"{self.title}: all {len(self.entry_rules)} entry rules met in regime "
                f"{regime['regime'].iloc[i]} (confidence {float(regime['regime_confidence'].iloc[i]):.2f}); "
                f"stop {stop:.6g} ({risk_pct:.2f}% below close){tgt}")


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else round(v, 8)


def htf_all_up(f: pd.DataFrame, confirm_timeframes: tuple[str, ...]) -> pd.Series:
    cols = [f"h{tf}_trend" for tf in confirm_timeframes if f"h{tf}_trend" in f]
    if not cols:
        return pd.Series(False, index=f.index)
    return (f[cols] == 1).all(axis=1)


def htf_none_down(f: pd.DataFrame, confirm_timeframes: tuple[str, ...]) -> pd.Series:
    cols = [f"h{tf}_trend" for tf in confirm_timeframes if f"h{tf}_trend" in f]
    if not cols:
        return pd.Series(False, index=f.index)
    known = f[cols].notna().all(axis=1)
    return known & (f[cols] >= 0).all(axis=1)


def slowest_down(f: pd.DataFrame, confirm_timeframes: tuple[str, ...]) -> pd.Series:
    if not confirm_timeframes:
        return pd.Series(False, index=f.index)
    col = f"h{confirm_timeframes[-1]}_trend"
    return f[col] == -1 if col in f else pd.Series(False, index=f.index)


def cost_ok(close: pd.Series, target: pd.Series, ctx: StrategyContext, ratio: float) -> pd.Series:
    """The expected move to the target must pay the round-trip costs ``ratio`` times over."""
    reward_pct = (target - close) / close * 100.0
    return reward_pct >= ratio * ctx.round_trip_cost_pct
