"""Baselines a strategy must beat to be interesting.

* Buy and hold of the same pairs over the same period (in the engine).
* Random entry: the same regimes, the same stop/target structure and roughly the same
  number of trades as the strategy, but entry timing drawn at random (seeded). If a strategy
  does not beat most random-timing runs, its entry rules add nothing.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd
from pydantic import Field

from app.backtesting.engine import BacktestSpec, prepare_views, run_backtest
from app.backtesting.metrics import compute_metrics
from app.config import Settings
from app.strategies.base import SignalFrame, Strategy, StrategyContext, StrategyParams
from app.strategies.registry import build


class RandomParams(StrategyParams):
    probability: float = Field(0.01, gt=0, le=1)
    stop_atr: float = Field(1.5, gt=0, le=10)
    target_r: float = Field(2.0, gt=0, le=10)
    time_stop_bars: int = Field(64, ge=1, le=5000)
    seed: int = 0


class RandomEntry(Strategy):
    name = "random_entry"
    title = "Random-entry baseline"
    code_version = "1.0"
    hypothesis = "baseline: entry timing carries no information"
    required_regimes = ("trend_up", "trend_down", "range", "high_volatility_breakout")
    entry_rules = ("random draw",)
    exit_rules = ("time stop", "hard stop or take profit")
    stop_rule = "stop_atr ATR below the close"
    target_rule = "target_r x risk"
    Params = RandomParams
    tunable = ()

    def __init__(self, params: dict[str, Any] | None = None, regimes: tuple[str, ...] | None = None) -> None:
        super().__init__(params)
        if regimes:
            self.required_regimes = regimes  # type: ignore[misc]

    def compute(self, f: pd.DataFrame, regime: pd.DataFrame, ctx: StrategyContext) -> SignalFrame:
        p: RandomParams = self.params  # type: ignore[assignment]
        rng = np.random.default_rng(p.seed)
        draw = pd.Series(rng.random(len(f)) < p.probability, index=f.index)
        allowed = regime["regime"].isin(self.required_regimes)
        stop = f["close"] - p.stop_atr * f["atr"]
        target = f["close"] + p.target_r * (f["close"] - stop)
        entry = draw & allowed & f["atr"].notna()
        none = pd.Series(False, index=f.index)
        return SignalFrame(entry=entry, stop=stop, target=target, invalidation=pd.Series(np.nan, index=f.index),
                           confidence=pd.Series(0.5, index=f.index), exit_signal=none,
                           exit_reason=pd.Series("", index=f.index), conditions={"random draw": entry})

    def time_stop_bars(self) -> int:
        return self.params.time_stop_bars  # type: ignore[attr-defined]


def random_baseline(candles: dict[str, pd.DataFrame], settings: Settings, base: BacktestSpec, *, runs: int,
                    strategy_trades: int, strategy_expectancy_r: float | None,
                    rules: Callable[[str], dict[str, Any]] | None = None, seed: int = 42) -> dict[str, Any]:
    if not base.strategy:
        raise ValueError("random baseline needs the strategy it is compared with")
    target = build(base.strategy, base.params)
    views = prepare_views(candles, base, settings)
    eligible_bars = 0
    for ps in views.values():
        mask = (ps.ts >= base.start) & (ps.ts <= base.end)
        eligible_bars += int(ps.view.regime["regime"][mask].isin(target.required_regimes).sum())
    if eligible_bars == 0:
        return {"available": False, "reason": "the strategy's regimes never occurred in this period"}
    probability = min(1.0, max(strategy_trades, 5) / eligible_bars)
    results = []
    for j in range(runs):
        rnd = RandomEntry({"probability": probability, "seed": seed + j,
                           "time_stop_bars": getattr(target, "time_stop_bars", lambda: 64)() or 64},
                          regimes=target.required_regimes)
        spec = BacktestSpec(**{**base.__dict__, "strategy": None, "label": f"{base.label}-random{j}"})
        res = run_backtest(spec, candles, settings, strategies={rnd.name: rnd}, precomputed=views, rules=rules,
                           keep_decisions=False)
        m = compute_metrics(res.trades, res.equity, initial_cash=base.initial_cash)
        results.append({"seed": seed + j, "trades": m["trades"], "expectancy_r": m["expectancy_r"],
                        "net_return_pct": m["net_return_pct"]})
    exp = [r["expectancy_r"] for r in results if r["expectancy_r"] is not None]
    percentile = None
    if strategy_expectancy_r is not None and exp:
        percentile = float(np.mean([strategy_expectancy_r > e for e in exp]) * 100)
    return {"available": True, "runs": runs, "probability": probability, "results": results,
            "random_expectancy_r_median": float(np.median(exp)) if exp else None,
            "strategy_beats_random_pct": percentile,
            "note": "share of random-timing runs (same regimes, stops and targets) the strategy beat on expectancy"}
