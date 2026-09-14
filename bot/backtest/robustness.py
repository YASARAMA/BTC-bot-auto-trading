"""How much of a backtest result is skill, and how much was the order of the cards.

Two questions a single backtest number cannot answer:

1. *Would this have worked if the same trades had arrived in a different order?* A curve
   that only looks good because the winners happened to come first is not a strategy.
   `monte_carlo` re-deals the trades thousands of times and reports the spread: the
   median outcome, the bad tail, and how often the account would have ended down.

2. *Does the result survive a small change to the settings?* If moving one parameter by a
   single step destroys the profit, the backtest found a coincidence in this particular
   history, not an edge. `sensitivity` sweeps one parameter at a time and says whether the
   best value sits on a plateau (robust) or on a spike (fitted to the past).

Neither of these makes a losing strategy profitable. They exist to stop a profitable-
looking backtest from being trusted more than it deserves.
"""
from __future__ import annotations

import logging
import statistics
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from bot.backtest.engine import run_backtest
from bot.backtest.optimize import OBJECTIVES, DEFAULT_OBJECTIVE, default_grid
from bot.config import BotConfig
from bot.models import Trade

log = logging.getLogger(__name__)

DEFAULT_RUNS = 2000
RUIN_DRAWDOWN_PCT = 50.0  # an account this far down is finished in practice
MIN_TRADES_FOR_CONFIDENCE = 30


# ---------------------------------------------------------------- Monte Carlo


def trade_returns(trades: Sequence[Trade], equity: pd.DataFrame) -> list[float]:
    """Each trade's profit as a fraction of the account it was taken with.

    Using the profit in dollars would let an early large position dominate the reshuffle;
    what carries over to a different order is the *proportion* of the account each trade
    won or lost.
    """
    if not trades or equity is None or equity.empty:
        return []
    ts = equity["ts"].to_numpy()
    eq = equity["equity"].to_numpy(dtype=float)
    out: list[float] = []
    for t in trades:
        idx = int(np.searchsorted(ts, t.entry_ts, side="right")) - 1
        before = float(eq[max(idx, 0)])
        if before > 0:
            out.append(float(t.pnl) / before)
    return out


def _path(returns: Sequence[float]) -> tuple[float, float]:
    """Compound one ordering of the trades.

    Returns the final multiple of the starting account and the worst drawdown along the
    way, as a positive percentage (20.0 means the account was once 20% below its peak).
    """
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= 1.0 + r
        if equity <= 0.0:  # a total wipeout: nothing left to trade with
            return 0.0, 100.0
        peak = max(peak, equity)
        worst = max(worst, (1.0 - equity / peak) * 100.0)
    return equity, worst


def _pct(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else 0.0


@dataclass
class MonteCarlo:
    method: str
    runs: int
    trades: int
    returns_pct: dict[str, float]
    drawdowns_pct: dict[str, float]
    prob_loss_pct: float
    prob_ruin_pct: float
    actual_return_pct: float
    actual_drawdown_pct: float
    verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method, "runs": self.runs, "trades": self.trades,
            "returns_pct": self.returns_pct, "drawdowns_pct": self.drawdowns_pct,
            "prob_loss_pct": self.prob_loss_pct, "prob_ruin_pct": self.prob_ruin_pct,
            "actual_return_pct": self.actual_return_pct, "actual_drawdown_pct": self.actual_drawdown_pct,
            "verdict": self.verdict,
        }


def monte_carlo(
    trades: Sequence[Trade],
    equity: pd.DataFrame,
    *,
    method: str = "resample",
    runs: int = DEFAULT_RUNS,
    seed: int = 7,
    ruin_drawdown_pct: float = RUIN_DRAWDOWN_PCT,
) -> MonteCarlo:
    """Re-deal the trades and report the spread of outcomes.

    `resample` draws trades with replacement: the answer to "what if the next year holds a
    different mix of these same kinds of trades". `shuffle` keeps exactly the trades that
    happened and only changes their order: because compounding is commutative the final
    return is then always the same, and only the drawdown moves - which is precisely the
    question of how lucky the sequence was.
    """
    if method not in ("resample", "shuffle"):
        raise ValueError("method must be 'resample' or 'shuffle'")
    returns = trade_returns(trades, equity)
    n = len(returns)
    if n < 2:
        raise ValueError(f"Monte Carlo needs at least 2 trades, the backtest produced {n}")

    rng = np.random.default_rng(seed)
    finals: list[float] = []
    draws: list[float] = []
    for _ in range(runs):
        if method == "resample":
            order = rng.integers(0, n, size=n)
        else:
            order = rng.permutation(n)
        final, worst = _path([returns[i] for i in order])
        finals.append((final - 1.0) * 100.0)
        draws.append(worst)

    actual_final, actual_dd = _path(returns)
    out = MonteCarlo(
        method=method, runs=runs, trades=n,
        returns_pct={"p05": round(_pct(finals, 5), 2), "p25": round(_pct(finals, 25), 2),
                     "median": round(_pct(finals, 50), 2), "p75": round(_pct(finals, 75), 2),
                     "p95": round(_pct(finals, 95), 2), "worst": round(min(finals), 2),
                     "best": round(max(finals), 2)},
        # Drawdowns are positive magnitudes here, so a higher percentile is a worse loss.
        drawdowns_pct={"median": round(_pct(draws, 50), 2), "p75": round(_pct(draws, 75), 2),
                       "p95": round(_pct(draws, 95), 2), "worst": round(max(draws), 2)},
        prob_loss_pct=round(sum(1 for f in finals if f <= 0) / runs * 100.0, 1),
        prob_ruin_pct=round(sum(1 for d in draws if d >= abs(ruin_drawdown_pct)) / runs * 100.0, 1),
        actual_return_pct=round((actual_final - 1.0) * 100.0, 2),
        actual_drawdown_pct=round(actual_dd, 2),
    )
    out.verdict = _mc_verdict(out)
    return out


def _mc_verdict(mc: MonteCarlo) -> str:
    """Plain language, and deliberately unkind: the numbers here are easy to over-read."""
    parts: list[str] = []
    if mc.trades < MIN_TRADES_FOR_CONFIDENCE:
        parts.append(f"Only {mc.trades} trades, so every number below is a rough sketch; "
                     f"{MIN_TRADES_FOR_CONFIDENCE}+ would be the minimum to lean on this.")
    median = mc.returns_pct["median"]
    p05 = mc.returns_pct["p05"]
    if mc.prob_ruin_pct >= 5.0:
        parts.append(f"In {mc.prob_ruin_pct:g}% of the re-deals the account lost half its value. "
                     f"That is a real risk of ruin: cut the size per trade before anything else.")
    if mc.prob_loss_pct >= 40.0:
        parts.append(f"{mc.prob_loss_pct:g}% of orderings ended down. The result depends on luck "
                     f"more than on the strategy - do not trade it with real money.")
    elif mc.prob_loss_pct >= 20.0:
        parts.append(f"{mc.prob_loss_pct:g}% of orderings ended down, so a losing year is entirely "
                     f"normal for this strategy even if the average is positive.")
    if median <= 0:
        parts.append(f"The median re-deal ends at {median:g}%: the typical outcome is not profit.")
    else:
        parts.append(f"The median re-deal ends at {median:+g}% with the bad fifth at {p05:+g}% "
                     f"and a typical worst drawdown of {mc.drawdowns_pct['median']:g}%.")
    if mc.actual_return_pct > mc.returns_pct["p95"]:
        parts.append("The backtest itself landed above 95% of the re-deals, which usually means "
                     "the history was kind to it rather than that the strategy is exceptional.")
    return " ".join(parts)


# ---------------------------------------------------------------- parameter sensitivity


@dataclass
class Sweep:
    param: str
    values: list[Any]
    scores: list[float | None]
    returns_pct: list[float | None]
    trades: list[int]
    baseline_value: Any
    best_value: Any = None
    best_score: float | None = None
    shape: str = "unknown"
    neighbour_drop_pct: float | None = None
    at_edge: bool = False  # the best value is the first or last one tried

    def to_dict(self) -> dict[str, Any]:
        return {
            "param": self.param, "values": self.values, "scores": self.scores,
            "returns_pct": self.returns_pct, "trades": self.trades,
            "baseline_value": self.baseline_value, "best_value": self.best_value,
            "best_score": self.best_score, "shape": self.shape,
            "neighbour_drop_pct": self.neighbour_drop_pct, "at_edge": self.at_edge,
        }


@dataclass
class Sensitivity:
    strategy: str
    baseline: dict[str, Any]
    baseline_score: float | None
    baseline_return_pct: float | None
    sweeps: list[Sweep] = field(default_factory=list)
    objective: str = DEFAULT_OBJECTIVE
    verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy, "baseline": self.baseline, "baseline_score": self.baseline_score,
            "baseline_return_pct": self.baseline_return_pct, "objective": self.objective,
            "sweeps": [s.to_dict() for s in self.sweeps], "verdict": self.verdict,
        }


def _run(args: tuple[pd.DataFrame, BotConfig, str | None, dict[str, Any], float | None]) -> dict[str, Any]:
    df, cfg, name, params, cash = args
    try:
        return run_backtest(df, cfg, strategy_name=name, params=params, initial_cash=cash).metrics
    except Exception as exc:  # noqa: BLE001 - one broken combination must not stop the sweep
        log.info("sensitivity run failed for %s: %s", params, exc)
        return {}


def _score_of(metrics: dict[str, Any], objective: str, min_trades: int) -> float | None:
    if not metrics or metrics.get("trades", 0) < min_trades:
        return None
    return round(float(OBJECTIVES[objective](metrics)), 4)


def _shape(scores: list[float | None], best_index: int) -> tuple[str, float | None]:
    """Is the best setting on a plateau or on a spike?

    A spike is a peak whose immediate neighbours are far worse: change the parameter by one
    step and the profit disappears. That is the signature of a value fitted to this exact
    history, and it is the single most useful thing a sweep can tell you.
    """
    usable = [s for s in scores if s is not None]
    if len(usable) < 3:
        return "too few points", None
    best = scores[best_index]
    if best is None or best <= 0:
        return "no profitable setting", None
    neighbours = [scores[i] for i in (best_index - 1, best_index + 1)
                  if 0 <= i < len(scores) and scores[i] is not None]
    if not neighbours:
        return "edge of the range", None
    drop = (best - statistics.fmean(neighbours)) / abs(best) * 100.0
    if max(usable) - min(usable) < 1e-9:
        return "flat", 0.0
    return ("spike" if drop >= 50.0 else "plateau"), round(drop, 1)


def sensitivity(
    df: pd.DataFrame,
    cfg: BotConfig,
    *,
    params: dict[str, Any] | None = None,
    strategy_name: str | None = None,
    grid: dict[str, Sequence[Any]] | None = None,
    objective: str = DEFAULT_OBJECTIVE,
    min_trades: int = 5,
    cash: float | None = None,
    workers: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Sensitivity:
    """Sweep one parameter at a time around a baseline and report the shape of each curve."""
    name = strategy_name or cfg.strategy.name
    space = dict(grid if grid is not None else default_grid(name))
    base = dict(params if params is not None else cfg.strategy.params)
    if not space:
        raise ValueError(f"no parameters to sweep for strategy {name!r}")

    jobs: list[tuple[pd.DataFrame, BotConfig, str | None, dict[str, Any], float | None]] = []
    index: list[tuple[str, int]] = []
    for key, values in space.items():
        for i, value in enumerate(values):
            jobs.append((df, cfg, name, {**base, key: value}, cash))
            index.append((key, i))
    jobs.append((df, cfg, name, dict(base), cash))  # the baseline itself, run once

    workers = workers if workers is not None else min(4, len(jobs))
    results: list[dict[str, Any]] = []
    if workers and workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, out in enumerate(pool.map(_run, jobs, chunksize=1), start=1):
                results.append(out)
                if progress:
                    progress(i, len(jobs))
    else:
        for i, job in enumerate(jobs, start=1):
            results.append(_run(job))
            if progress:
                progress(i, len(jobs))

    baseline_metrics = results[-1]
    out = Sensitivity(
        strategy=name, baseline=base, objective=objective,
        baseline_score=_score_of(baseline_metrics, objective, min_trades),
        baseline_return_pct=baseline_metrics.get("total_return_pct"),
    )
    by_param: dict[str, list[dict[str, Any]]] = {k: [{} for _ in v] for k, v in space.items()}
    for (key, i), metrics in zip(index, results):
        by_param[key][i] = metrics

    for key, values in space.items():
        metrics_list = by_param[key]
        scores = [_score_of(m, objective, min_trades) for m in metrics_list]
        sweep = Sweep(
            param=key, values=list(values), scores=scores,
            returns_pct=[m.get("total_return_pct") for m in metrics_list],
            trades=[int(m.get("trades", 0) or 0) for m in metrics_list],
            baseline_value=base.get(key),
        )
        if not any(sweep.trades):
            sweep.shape = "no trades"
        usable = [(i, s) for i, s in enumerate(scores) if s is not None]
        if usable:
            best_index, best_score = max(usable, key=lambda pair: pair[1])
            sweep.best_value, sweep.best_score = list(values)[best_index], best_score
            sweep.shape, sweep.neighbour_drop_pct = _shape(scores, best_index)
            sweep.at_edge = best_index in (0, len(scores) - 1)
        out.sweeps.append(sweep)

    out.verdict = _sensitivity_verdict(out)
    return out


def _sensitivity_verdict(s: Sensitivity) -> str:
    spikes = [sw.param for sw in s.sweeps if sw.shape == "spike"]
    plateaus = [sw.param for sw in s.sweeps if sw.shape == "plateau"]
    dead = [sw.param for sw in s.sweeps if sw.shape in ("no profitable setting", "no trades")]
    edges = [sw.param for sw in s.sweeps if sw.at_edge and sw.shape in ("plateau", "spike")]
    parts: list[str] = []
    if len(dead) == len(s.sweeps) and s.sweeps:
        silent = all(sw.shape == "no trades" for sw in s.sweeps)
        if silent:
            return ("No setting of any parameter produced a single trade on this data. Either the "
                    "history is too short for the warmup, or the entry conditions never occurred.")
        return ("No setting of any parameter made this strategy profitable on this data. "
                "The problem is the strategy or the market, not the tuning.")
    if spikes:
        parts.append("Fitted to the past: " + ", ".join(spikes) + " - the profit disappears when the "
                     "value moves by one step, which means it was chosen for this history. Prefer a "
                     "setting in the middle of a wide, boring plateau over the peak.")
    if plateaus:
        parts.append("Robust: " + ", ".join(plateaus) + " - the result holds across neighbouring "
                     "values, which is what a real edge looks like.")
    if dead:
        parts.append("No profitable setting at all for: " + ", ".join(dead) + ".")
    if edges:
        parts.append("The best value sits at the end of the tested range for " + ", ".join(edges) +
                     ", so the real optimum may lie outside it - widen the range before concluding.")
    if not parts:
        parts.append("Not enough usable runs to judge the shape of the curves. Use a longer history "
                     "or lower the minimum number of trades.")
    return " ".join(parts)


# ---------------------------------------------------------------- CLI


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json
    import sys

    from bot.config import load_config
    from bot.data.history import load_csv
    from bot.logging_utils import setup_logging

    p = argparse.ArgumentParser(
        prog="python -m bot.backtest.robustness",
        description="Ask whether a backtest result survives a reshuffle and a nudge of its settings.")
    p.add_argument("--csv", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--strategy", default=None)
    p.add_argument("--mode", choices=["monte-carlo", "sensitivity", "both"], default="both")
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    p.add_argument("--method", choices=["resample", "shuffle"], default="resample")
    p.add_argument("--objective", choices=sorted(OBJECTIVES), default=DEFAULT_OBJECTIVE)
    p.add_argument("--min-trades", type=int, default=5)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    setup_logging("WARNING", "text")
    cfg = load_config(args.config)
    df = load_csv(args.csv)
    print(f"{len(df)} candles", file=sys.stderr)
    payload: dict[str, Any] = {}

    if args.mode in ("monte-carlo", "both"):
        result = run_backtest(df, cfg, strategy_name=args.strategy)
        mc = monte_carlo(result.trades, result.equity, method=args.method, runs=args.runs, seed=args.seed)
        payload["monte_carlo"] = mc.to_dict()
        if not args.json:
            print(f"\nMonte Carlo ({mc.method}, {mc.runs} runs over {mc.trades} trades)")
            print(f"  actual        {mc.actual_return_pct:>9.2f}%   drawdown {mc.actual_drawdown_pct:.2f}%")
            for label, key in (("bad fifth", "p05"), ("median", "median"), ("good fifth", "p95")):
                print(f"  {label:<13} {mc.returns_pct[key]:>9.2f}%")
            print(f"  ended down    {mc.prob_loss_pct:>9.1f}% of runs")
            print(f"  lost half     {mc.prob_ruin_pct:>9.1f}% of runs")
            print(f"\n  {mc.verdict}")

    if args.mode in ("sensitivity", "both"):
        def tick(done: int, total: int) -> None:
            print(f"\r  {done}/{total} runs", end="", file=sys.stderr, flush=True)

        sens = sensitivity(df, cfg, strategy_name=args.strategy, objective=args.objective,
                           min_trades=args.min_trades, workers=args.workers, progress=tick)
        print(file=sys.stderr)
        payload["sensitivity"] = sens.to_dict()
        if not args.json:
            print(f"\nParameter sensitivity ({sens.strategy}, objective {sens.objective})")
            for sw in sens.sweeps:
                best = f"{sw.best_value}" if sw.best_value is not None else "-"
                drop = f"{sw.neighbour_drop_pct:.0f}%" if sw.neighbour_drop_pct is not None else "-"
                print(f"  {sw.param:<24} best={best:<8} shape={sw.shape:<22} drop vs neighbours={drop}")
            print(f"\n  {sens.verdict}")

    if args.json:
        print(_json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
