"""Parameter search and walk-forward testing.

A single backtest is easy to fool yourself with: try enough parameter sets and one of them
will look brilliant on the period you tried it on. Two tools here guard against that.

`grid_search` runs every combination (or a random sample) and ranks them, so you can see
the whole surface rather than one lucky point.

`walk_forward` is the honest test. It cuts the history into consecutive blocks, optimises
on each in-sample block and then trades the parameters it chose on the *next* block, which
the optimiser never saw. The equity curve it returns is stitched from out-of-sample results
only. If that curve does not make money, the strategy does not work, however good the
in-sample numbers look.
"""
from __future__ import annotations

import itertools
import logging
import math
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import pandas as pd

from bot.backtest.engine import run_backtest
from bot.backtest.metrics import compute_metrics
from bot.common import iso, timeframe_to_ms
from bot.config import BotConfig
from bot.models import Trade

log = logging.getLogger(__name__)

# Ranking objectives. Each takes a metrics dict and returns "higher is better".
OBJECTIVES: dict[str, Callable[[dict[str, Any]], float]] = {
    "return": lambda m: m["total_return_pct"],
    "sharpe": lambda m: m["sharpe"],
    # Return per unit of pain: the measure that punishes a curve that makes 40% after a 39% fall.
    "return_over_drawdown": lambda m: m["total_return_pct"] / max(1.0, abs(m["max_drawdown_pct"])),
    # A missing profit factor means there were no losing trades at all: treat it as excellent.
    "profit_factor": lambda m: 10.0 if m.get("no_losing_trades") else (m.get("profit_factor") or 0.0),
    # Money per trade. Ranking by this refuses to buy a win rate with a smaller average win.
    "expectancy": lambda m: m.get("expectancy", 0.0),
    # Win rate, for when that is genuinely what you are after. Kept honest by min_win_rate
    # and min_trades: on its own it is the easiest metric in trading to fake.
    "win_rate": lambda m: m.get("win_rate_pct", 0.0),
}
DEFAULT_OBJECTIVE = "return_over_drawdown"

# A sensible default search space for the reference strategy.
DEFAULT_GRID: dict[str, list[Any]] = {
    "ema_fast": [8, 12, 16, 21],
    "ema_slow": [26, 34, 50, 100],
    "rsi_buy_min": [40.0, 45.0, 50.0],
    "rsi_buy_max": [65.0, 70.0, 80.0],
    "atr_stop_mult": [1.5, 2.0, 3.0],
    "atr_tp_mult": [2.0, 3.0, 4.5],
    "trend_filter_period": [0, 100, 200],
}

# Each strategy has its own parameter names; searching one strategy's grid over another
# would only produce settings it rejects.
GRIDS: dict[str, dict[str, list[Any]]] = {
    "ema_rsi": DEFAULT_GRID,
    "breakout": {
        "entry_period": [10, 20, 30, 55],
        "exit_period": [5, 10, 20],
        "atr_stop_mult": [1.5, 2.0, 3.0],
        "min_breakout_atr": [0.0, 0.25, 0.5],
        "trend_filter_period": [0, 100, 200],
        "volume_min_ratio": [0.0, 1.2, 1.5],
    },
    "mean_reversion": {
        "bb_period": [14, 20, 30],
        "bb_std": [1.5, 2.0, 2.5],
        "rsi_buy_max": [25.0, 35.0, 45.0],
        "atr_stop_mult": [2.0, 2.5, 3.0],
        "exit_band": ["middle", "upper"],
        "trend_filter_period": [0, 100, 200],
    },
    "regime": {
        "adx_period": [10, 14, 20],
        "trend_above": [22.0, 25.0, 30.0],
        "range_below": [15.0, 20.0, 25.0],
    },
    "buy_hold": {"stop_loss_pct": [0.0, 20.0, 35.0, 50.0]},
}


def default_grid(strategy: str | None) -> dict[str, list[Any]]:
    """The search space for a strategy, or a clear refusal when searching makes no sense."""
    name = strategy or "ema_rsi"
    if name == "ai":
        raise ValueError(
            "searching over the 'ai' strategy would call the Claude API once per candle per "
            "combination, which costs real money. Search 'ema_rsi' instead and copy the winner "
            "into strategy.params.indicator_params.")
    try:
        return GRIDS[name]
    except KeyError as exc:
        raise ValueError(f"no default search space for strategy {name!r}; pass one explicitly") from exc


def expand_grid(grid: dict[str, Sequence[Any]], *, sample: int | None = None,
                seed: int = 7) -> list[dict[str, Any]]:
    """Every combination in the grid, minus the impossible ones, optionally sampled."""
    keys = list(grid)
    combos = [dict(zip(keys, values)) for values in itertools.product(*(list(grid[k]) for k in keys))]
    combos = [c for c in combos if _is_valid(c)]
    if sample is not None and 0 < sample < len(combos):
        combos = random.Random(seed).sample(combos, sample)
    return combos


def _is_valid(params: dict[str, Any]) -> bool:
    """Drop combinations the strategies would reject, so they never reach a backtest."""
    if "ema_fast" in params and "ema_slow" in params and params["ema_fast"] >= params["ema_slow"]:
        return False
    if "rsi_buy_min" in params and "rsi_buy_max" in params and params["rsi_buy_min"] >= params["rsi_buy_max"]:
        return False
    if "entry_period" in params and "exit_period" in params and params["exit_period"] > params["entry_period"]:
        return False
    if "range_below" in params and "trend_above" in params and params["range_below"] > params["trend_above"]:
        return False
    return True


@dataclass
class Candidate:
    params: dict[str, Any]
    metrics: dict[str, Any]
    score: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"params": self.params, "score": round(self.score, 4), "error": self.error,
                "metrics": {k: self.metrics.get(k) for k in
                            ("total_return_pct", "max_drawdown_pct", "sharpe", "trades", "win_rate_pct",
                             "profit_factor", "exposure_pct", "final_equity")}}


@dataclass
class WalkForwardFold:
    index: int
    train: tuple[int, int]
    test: tuple[int, int]
    chosen: dict[str, Any]
    train_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    trades: list[Trade] = field(default_factory=list)
    equity: pd.DataFrame | None = None

    def to_dict(self) -> dict[str, Any]:
        keep = ("total_return_pct", "max_drawdown_pct", "sharpe", "trades", "win_rate_pct", "profit_factor")
        return {
            "index": self.index,
            "train": {"from": iso(self.train[0]), "to": iso(self.train[1])},
            "test": {"from": iso(self.test[0]), "to": iso(self.test[1])},
            "chosen": self.chosen,
            "train_metrics": {k: self.train_metrics.get(k) for k in keep},
            "test_metrics": {k: self.test_metrics.get(k) for k in keep},
        }


def _score(metrics: dict[str, Any], objective: str, min_trades: int, min_win_rate: float = 0.0) -> float:
    """Score a result, refusing to reward a curve built on a handful of trades.

    `min_win_rate` is a floor, not a goal: it throws away settings that win too rarely to
    sit through, while the objective still decides which of the survivors is best. Ranking
    by win rate alone would pick the one that takes a tiny profit every time and gives it
    all back on the losers.
    """
    if metrics.get("trades", 0) < min_trades:
        return float("-inf")
    if min_win_rate and metrics.get("win_rate_pct", 0.0) < min_win_rate:
        return float("-inf")
    fn = OBJECTIVES.get(objective, OBJECTIVES[DEFAULT_OBJECTIVE])
    value = fn(metrics)
    return float("-inf") if value is None or (isinstance(value, float) and math.isnan(value)) else float(value)


def _run_one(args: tuple[pd.DataFrame, BotConfig, str | None, dict[str, Any], float | None]) -> dict[str, Any]:
    df, cfg, strategy_name, params, cash = args
    try:
        result = run_backtest(df, cfg, strategy_name=strategy_name, params=params, initial_cash=cash)
        return {"params": params, "metrics": result.metrics}
    except Exception as exc:  # noqa: BLE001 - one bad combination must not stop the search
        return {"params": params, "metrics": {}, "error": f"{type(exc).__name__}: {exc}"}


def grid_search(
    df: pd.DataFrame,
    cfg: BotConfig,
    *,
    grid: dict[str, Sequence[Any]] | None = None,
    combos: Iterable[dict[str, Any]] | None = None,
    strategy_name: str | None = None,
    objective: str = DEFAULT_OBJECTIVE,
    min_trades: int = 10,
    min_win_rate: float = 0.0,
    sample: int | None = None,
    cash: float | None = None,
    workers: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[Candidate]:
    """Backtest every parameter combination and return them ranked, best first."""
    base = dict(cfg.strategy.params)
    todo = (list(combos) if combos is not None
            else expand_grid(grid or default_grid(strategy_name or cfg.strategy.name), sample=sample))
    if not todo:
        raise ValueError("the search space is empty")
    jobs = [(df, cfg, strategy_name, {**base, **params}, cash) for params in todo]

    results: list[dict[str, Any]] = []
    workers = workers if workers is not None else min(4, len(jobs))
    if workers and workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, out in enumerate(pool.map(_run_one, jobs, chunksize=1), start=1):
                results.append(out)
                if progress:
                    progress(i, len(jobs))
    else:
        for i, job in enumerate(jobs, start=1):
            results.append(_run_one(job))
            if progress:
                progress(i, len(jobs))

    candidates = [
        Candidate(params={k: v for k, v in r["params"].items() if k in (todo[0] if todo else {})},
                  metrics=r["metrics"], score=_score(r["metrics"], objective, min_trades, min_win_rate),
                  error=r.get("error"))
        for r in results
    ]
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def walk_forward(
    df: pd.DataFrame,
    cfg: BotConfig,
    *,
    grid: dict[str, Sequence[Any]] | None = None,
    folds: int = 4,
    train_ratio: float = 0.7,
    strategy_name: str | None = None,
    objective: str = DEFAULT_OBJECTIVE,
    min_trades: int = 5,
    min_win_rate: float = 0.0,
    sample: int | None = None,
    cash: float | None = None,
    workers: int | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Optimise on each block, then trade the winner on the block that follows it."""
    if folds < 1:
        raise ValueError("folds must be at least 1")
    warm = _warmup_for(cfg, strategy_name)
    total = len(df)
    block = total // folds
    if block < warm * 2:
        raise ValueError(
            f"not enough candles for {folds} folds: each block would be {block} candles and the "
            f"strategy needs {warm} just to warm up. Use fewer folds or a longer history.")

    combos = expand_grid(grid or default_grid(strategy_name or cfg.strategy.name), sample=sample)
    results: list[WalkForwardFold] = []
    equities: list[pd.DataFrame] = []
    all_trades: list[Trade] = []
    cash_now = cash if cash is not None else cfg.paper.initial_cash

    for i in range(folds):
        start = i * block
        stop = total if i == folds - 1 else (i + 1) * block
        split = start + int((stop - start) * train_ratio)
        train, test = df.iloc[start:split].reset_index(drop=True), df.iloc[max(0, split - warm):stop].reset_index(drop=True)
        if len(train) < warm + 2 or len(test) < warm + 2:
            continue
        if progress:
            progress("train", i + 1, folds)
        ranked = grid_search(train, cfg, combos=combos, strategy_name=strategy_name, objective=objective,
                             min_trades=min_trades, min_win_rate=min_win_rate, cash=cash_now, workers=workers)
        best = next((c for c in ranked if c.score > float("-inf")), None)
        if best is None:
            continue
        if progress:
            progress("test", i + 1, folds)
        out = run_backtest(test, cfg, strategy_name=strategy_name, params=best.params, initial_cash=cash_now)
        # Compound: the next block starts with what the previous one finished with.
        cash_now = float(out.metrics["final_equity"])
        results.append(WalkForwardFold(
            index=i + 1, train=(int(train["ts"].iloc[0]), int(train["ts"].iloc[-1])),
            test=(int(test["ts"].iloc[0]), int(test["ts"].iloc[-1])), chosen=best.params,
            train_metrics=best.metrics, test_metrics=out.metrics, trades=out.trades, equity=out.equity))
        if len(out.equity):
            equities.append(out.equity)
        all_trades.extend(out.trades)

    if not results:
        raise ValueError("no fold produced a usable result; try fewer folds or a smaller minimum trade count")

    curve = pd.concat(equities, ignore_index=True).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    start_cash = cash if cash is not None else cfg.paper.initial_cash
    combined = compute_metrics(curve, all_trades, tf_ms=timeframe_to_ms(cfg.exchange.timeframe),
                               initial_cash=start_cash, first_price=float(df["close"].iloc[0]),
                               last_price=float(df["close"].iloc[-1]))
    stability = _stability(results)
    return {
        "folds": [f.to_dict() for f in results],
        "out_of_sample": combined,
        "equity": [{"ts": int(r.ts), "equity": float(r.equity)} for r in curve.itertuples()],
        "trades": [t.to_dict() for t in all_trades],
        "objective": objective,
        "combinations": len(combos),
        "stability": stability,
        "verdict": _verdict(combined, stability, results),
    }


def _warmup_for(cfg: BotConfig, strategy_name: str | None) -> int:
    from bot.strategy import get_strategy

    return get_strategy(strategy_name or cfg.strategy.name, cfg.strategy.params).warmup


def _stability(folds: list[WalkForwardFold]) -> dict[str, Any]:
    """How much the chosen parameters and the results jump around between blocks."""
    profitable = sum(1 for f in folds if f.test_metrics.get("total_return_pct", 0) > 0)
    keys = sorted({k for f in folds for k in f.chosen})
    changes = {k: len({str(f.chosen.get(k)) for f in folds}) for k in keys}
    in_sample = [f.train_metrics.get("total_return_pct", 0.0) for f in folds]
    out_sample = [f.test_metrics.get("total_return_pct", 0.0) for f in folds]
    return {
        "folds": len(folds),
        "profitable_folds": profitable,
        "distinct_values_per_parameter": changes,
        "avg_in_sample_return_pct": round(sum(in_sample) / len(in_sample), 2) if in_sample else 0.0,
        "avg_out_of_sample_return_pct": round(sum(out_sample) / len(out_sample), 2) if out_sample else 0.0,
    }


def _verdict(metrics: dict[str, Any], stability: dict[str, Any], folds: list[WalkForwardFold]) -> str:
    """One honest sentence about whether this strategy survived the test."""
    ret = metrics.get("total_return_pct", 0.0)
    hold = metrics.get("buy_hold_return_pct", 0.0)
    good = stability["profitable_folds"]
    total = stability["folds"]
    drop = stability["avg_in_sample_return_pct"] - stability["avg_out_of_sample_return_pct"]
    if ret <= 0:
        return (f"Out of sample this strategy lost {abs(ret):.1f}%. Tuning found parameters that fit the past, "
                f"not an edge that holds. Do not trade it with real money.")
    if good < total / 2:
        return (f"It ended up {ret:.1f}% but only {good} of {total} blocks made money: the result rests on a "
                f"lucky stretch rather than a repeatable edge.")
    if ret < hold:
        return (f"It made {ret:.1f}% out of sample while simply holding made {hold:.1f}%. Positive, but it does "
                f"not beat buying and holding on this data.")
    return (f"It made {ret:.1f}% out of sample against {hold:.1f}% for buy and hold, profitable in {good} of "
            f"{total} blocks. In-sample returns dropped by {drop:.1f} points out of sample, which is the "
            f"honest expectation to carry forward.")


# ----- command line ---------------------------------------------------------------------------
def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json
    import sys

    from bot.config import load_config
    from bot.data.history import load_csv
    from bot.logging_utils import setup_logging

    p = argparse.ArgumentParser(
        prog="python -m bot.backtest.optimize",
        description="Search strategy parameters and test them on data the search never saw.")
    p.add_argument("--csv", required=True, help="candles to use")
    p.add_argument("--config", default=None)
    p.add_argument("--strategy", default=None)
    p.add_argument("--mode", choices=["grid", "walk-forward"], default="walk-forward")
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--train-ratio", type=float, default=0.7)
    p.add_argument("--sample", type=int, default=60, help="random combinations to try (0 = the whole grid)")
    p.add_argument("--objective", choices=sorted(OBJECTIVES), default=DEFAULT_OBJECTIVE)
    p.add_argument("--min-trades", type=int, default=10)
    p.add_argument("--min-win-rate", type=float, default=0.0,
                   help="discard settings whose win rate is below this %% (a floor, not a goal)")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    setup_logging("WARNING", "text")
    cfg = load_config(args.config)
    df = load_csv(args.csv)
    sample = args.sample or None
    print(f"{len(df)} candles from {iso(int(df['ts'].iloc[0]))} to {iso(int(df['ts'].iloc[-1]))}", file=sys.stderr)

    if args.mode == "grid":
        def tick(done: int, total: int) -> None:
            print(f"\r  {done}/{total} combinations", end="", file=sys.stderr, flush=True)

        ranked = grid_search(df, cfg, strategy_name=args.strategy, objective=args.objective,
                             min_trades=args.min_trades, min_win_rate=args.min_win_rate, sample=sample,
                             workers=args.workers, progress=tick)
        print(file=sys.stderr)
        if args.json:
            print(_json.dumps([c.to_dict() for c in ranked[: args.top]], indent=2, default=str))
            return 0
        print(f"\nTop {min(args.top, len(ranked))} of {len(ranked)} by {args.objective}:")
        print(f"  {'return':>9} {'drawdown':>9} {'sharpe':>7} {'trades':>7}  parameters")
        for c in ranked[: args.top]:
            m = c.metrics
            if not m:
                continue
            print(f"  {m['total_return_pct']:>8.2f}% {m['max_drawdown_pct']:>8.2f}% {m['sharpe']:>7.2f} "
                  f"{m['trades']:>7}  {c.params}")
        print("\nThese are in-sample numbers: the search saw this whole period. Run --mode walk-forward "
              "before believing any of them.")
        return 0

    def wf_tick(phase: str, fold: int, total: int) -> None:
        print(f"\r  fold {fold}/{total}: {phase}...      ", end="", file=sys.stderr, flush=True)

    out = walk_forward(df, cfg, folds=args.folds, train_ratio=args.train_ratio, strategy_name=args.strategy,
                       objective=args.objective, min_trades=max(3, args.min_trades // 2),
                       min_win_rate=args.min_win_rate, sample=sample, workers=args.workers, progress=wf_tick)
    print(file=sys.stderr)
    if args.json:
        print(_json.dumps(out, indent=2, default=str))
        return 0
    m, s = out["out_of_sample"], out["stability"]
    print("=" * 72)
    print(f"Walk-forward over {s['folds']} blocks, {out['combinations']} parameter sets tried per block")
    print("-" * 72)
    for f in out["folds"]:
        print(f"  block {f['index']}  train {f['train']['from'][:10]}..{f['train']['to'][:10]} "
              f"{f['train_metrics']['total_return_pct']:>7.2f}%   "
              f"test {f['test']['from'][:10]}..{f['test']['to'][:10]} "
              f"{f['test_metrics']['total_return_pct']:>7.2f}%")
        print(f"           chose {f['chosen']}")
    print("-" * 72)
    print(f"  {'Out of sample return':<28}{m['total_return_pct']:>10.2f}%")
    print(f"  {'Buy and hold over the same':<28}{m['buy_hold_return_pct']:>10.2f}%")
    print(f"  {'Max drawdown':<28}{m['max_drawdown_pct']:>10.2f}%")
    print(f"  {'Sharpe':<28}{m['sharpe']:>11.2f}")
    print(f"  {'Trades':<28}{m['trades']:>11}")
    print(f"  {'Blocks in profit':<28}{s['profitable_folds']:>7} of {s['folds']}")
    print("-" * 72)
    print(f"  {out['verdict']}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
