"""Parameter sensitivity: does the result survive small changes to each tunable parameter?

Each tunable parameter is moved down and up by ``step`` (25% by default; integers rounded,
invalid combinations skipped) with everything else fixed, on the development segment only.
Stability is the share of neighbours that keep a positive expectancy. A strategy whose edge
disappears when one parameter moves a little is fitted to noise.
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from app.backtesting.engine import BacktestSpec, prepare_views, run_backtest
from app.backtesting.metrics import compute_metrics
from app.config import Settings
from app.strategies.registry import build


def neighbours(strategy: str, params: dict[str, Any], step: float = 0.25) -> list[tuple[str, Any, dict[str, Any]]]:
    base = build(strategy, params)
    out = []
    for name in base.tunable:
        value = base.params_dict[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for factor in (1 - step, 1 + step):
            new = value * factor
            if isinstance(value, int):
                new = int(round(new))
                if new == value:
                    new = value + (1 if factor > 1 else -1)
            candidate = {**base.params_dict, name: new}
            try:
                build(strategy, candidate)
            except Exception:  # noqa: BLE001 - outside the allowed range: skip
                continue
            out.append((name, new, candidate))
    return out


def sensitivity(candles: dict[str, pd.DataFrame], settings: Settings, base: BacktestSpec, *,
                rules: Callable[[str], dict[str, Any]] | None = None,
                progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    assert base.strategy
    views = prepare_views(candles, base, settings)
    ref = run_backtest(base, candles, settings, precomputed=views, rules=rules, keep_decisions=False)
    ref_m = compute_metrics(ref.trades, ref.equity, initial_cash=base.initial_cash)
    rows = []
    cands = neighbours(base.strategy, base.params)
    for j, (name, value, params) in enumerate(cands):
        spec = BacktestSpec(**{**base.__dict__, "params": params, "label": f"{base.label}-sens-{name}"})
        res = run_backtest(spec, candles, settings, precomputed=views, rules=rules, keep_decisions=False)
        m = compute_metrics(res.trades, res.equity, initial_cash=base.initial_cash)
        rows.append({"param": name, "value": value, "base_value": build(base.strategy, base.params).params_dict[name],
                     "trades": m["trades"], "expectancy_r": m["expectancy_r"], "net_return_pct": m["net_return_pct"],
                     "profit_factor": m["profit_factor"]})
        if progress:
            progress((j + 1) / max(1, len(cands)), f"sensitivity {name}={value}")
    valid = [r for r in rows if r["trades"] > 0]
    positive = [r for r in valid if (r["expectancy_r"] or 0) > 0]
    stability = len(positive) / len(valid) if valid else None
    return {"base": {"trades": ref_m["trades"], "expectancy_r": ref_m["expectancy_r"],
                     "net_return_pct": ref_m["net_return_pct"], "profit_factor": ref_m["profit_factor"]},
            "neighbours": rows, "stability": stability,
            "note": "share of neighbouring parameter sets (each tunable moved by 25%) with positive expectancy"}
