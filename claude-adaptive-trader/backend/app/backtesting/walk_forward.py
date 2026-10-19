"""Walk-forward evaluation over the research span (development + validation, never the test).

The span is cut into ``k`` rolling windows. In each, parameters may be chosen on the train
part (from a small, predefined grid, by expectancy in R with a minimum trade count, ties to
the simpler/default choice) and are then evaluated on the following, unseen test part. The
out-of-sample parts are concatenated into one OOS record. With no grid, the walk-forward
measures how stable a fixed strategy is from window to window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from app.backtesting.engine import BacktestResult, BacktestSpec, prepare_views, run_backtest
from app.backtesting.metrics import compute_metrics
from app.config import Settings
from app.core.timeframes import tf_ms
from app.execution.core import PairState


@dataclass
class Window:
    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass
class WalkForwardResult:
    windows: list[dict[str, Any]]
    oos_trades: list[Any]
    oos_equity: list[dict[str, Any]]
    chosen_params: list[dict[str, Any]]
    oos_metrics: dict[str, Any]
    profitable_window_ratio: float | None
    param_consistency: float | None
    notes: list[str] = field(default_factory=list)


def make_windows(start: int, end: int, timeframe: str, k: int, train_ratio: float) -> list[Window]:
    step = tf_ms(timeframe)
    bars = (end - start) // step + 1
    width = bars / (1 + (k - 1) * (1 - train_ratio))
    test_len = max(1, int(width * (1 - train_ratio)))
    train_len = max(1, int(width) - test_len)
    out = []
    for j in range(k):
        w0 = start + j * test_len * step
        train_end = w0 + (train_len - 1) * step
        test_start = train_end + step
        test_end = end if j == k - 1 else test_start + (test_len - 1) * step
        if test_start > end:
            break
        out.append(Window(j + 1, w0, train_end, test_start, min(test_end, end)))
    return out


def walk_forward(candles: dict[str, pd.DataFrame], settings: Settings, base: BacktestSpec, *, k: int,
                 train_ratio: float, grid: list[dict[str, Any]] | None = None, min_trades: int = 5,
                 rules: Callable[[str], dict[str, Any]] | None = None,
                 progress: Callable[[float, str], None] | None = None) -> WalkForwardResult:
    views: dict[str, PairState] = prepare_views(candles, base, settings)
    windows = make_windows(base.start, base.end, base.timeframe, k, train_ratio)
    oos_trades: list[Any] = []
    oos_equity: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    chosen: list[dict[str, Any]] = []
    equity_carry = base.initial_cash
    total_steps = len(windows) * ((len(grid) if grid else 0) + 1)
    done = 0
    for w in windows:
        params = dict(base.params)
        train_scores = []
        if grid:
            best_score, best = None, params
            for candidate in grid:
                merged = {**base.params, **candidate}
                spec = BacktestSpec(**{**base.__dict__, "params": merged, "start": w.train_start, "end": w.train_end,
                                       "label": f"{base.label}-wf{w.index}-train"})
                res = run_backtest(spec, candles, settings, precomputed=views, rules=rules, keep_decisions=False)
                m = compute_metrics(res.trades, res.equity, initial_cash=spec.initial_cash)
                score = m["expectancy_r"] if m["trades"] >= min_trades and m["expectancy_r"] is not None else None
                train_scores.append({"params": candidate, "trades": m["trades"], "expectancy_r": m["expectancy_r"]})
                if score is not None and (best_score is None or score > best_score + 1e-12):
                    best_score, best = score, merged
                done += 1
                if progress:
                    progress(done / total_steps, f"window {w.index}: train {len(train_scores)}/{len(grid)}")
            params = best
        spec = BacktestSpec(**{**base.__dict__, "params": params, "start": w.test_start, "end": w.test_end,
                               "initial_cash": equity_carry, "label": f"{base.label}-wf{w.index}-test"})
        res: BacktestResult = run_backtest(spec, candles, settings, precomputed=views, rules=rules,
                                           keep_decisions=False)
        m = compute_metrics(res.trades, res.equity, initial_cash=equity_carry)
        done += 1
        if progress:
            progress(done / total_steps, f"window {w.index}: out-of-sample")
        oos_trades.extend(res.trades)
        oos_equity.extend(res.equity)
        if res.equity:
            equity_carry = float(res.equity[-1]["equity"])
        chosen.append({k2: params.get(k2) for k2 in sorted(params)})
        rows.append({"window": w.index, "train": [w.train_start, w.train_end], "test": [w.test_start, w.test_end],
                     "params": params, "trades": m["trades"], "net_return_pct": m["net_return_pct"],
                     "expectancy_r": m["expectancy_r"], "profit_factor": m["profit_factor"],
                     "max_drawdown_pct": m["max_drawdown_pct"], "train_scores": train_scores})
    oos_metrics = compute_metrics(oos_trades, oos_equity, initial_cash=base.initial_cash)
    eligible = [r for r in rows]
    profitable = [r for r in eligible if r["trades"] >= min_trades and (r["net_return_pct"] or 0) > 0]
    ratio = len(profitable) / len(eligible) if eligible else None
    consistency = None
    if chosen and grid:
        keys = [str(sorted(c.items())) for c in chosen]
        consistency = max(keys.count(x) for x in set(keys)) / len(keys)
    notes = []
    if any(r["trades"] < min_trades for r in rows):
        notes.append(f"{sum(r['trades'] < min_trades for r in rows)} window(s) had fewer than {min_trades} trades "
                     f"and count as not profitable")
    return WalkForwardResult(rows, oos_trades, oos_equity, chosen, oos_metrics, ratio, consistency, notes)
