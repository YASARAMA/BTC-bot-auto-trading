"""Preliminary research acceptance gates.

They are screening rules, not proof: passing them means a strategy deserves a forward test,
not that it will make money. Failing any one of them means the evidence does not show a
reliable edge, and the verdict says exactly that.
"""
from __future__ import annotations

from typing import Any

from app.config import GateConfig, RiskConfig

PASSED = "passed_preliminary_gates"
NO_EDGE = "no_reliable_edge"


def _gate(name: str, passed: bool, value: Any, threshold: Any, detail: str) -> dict[str, Any]:
    return {"gate": name, "passed": bool(passed), "value": value, "threshold": threshold, "detail": detail}


def evaluate_gates(*, oos: dict[str, Any], stress: dict[str, Any] | None, stability: float | None,
                   window_ratio: float | None, bootstrap: dict[str, Any] | None, cfg: GateConfig,
                   risk: RiskConfig, min_trades_per_environment: int | None = None) -> dict[str, Any]:
    gates = []
    exp = oos.get("expectancy_r")
    gates.append(_gate("positive_expectancy", exp is not None and exp > cfg.min_expectancy_r, exp,
                       f"> {cfg.min_expectancy_r} R", "net expectancy per trade on out-of-sample data"))
    pf = oos.get("profit_factor")
    gates.append(_gate("profit_factor", pf is not None and pf > cfg.min_profit_factor, pf,
                       f"> {cfg.min_profit_factor}", "gross wins / gross losses, after costs"
                       + ("" if pf is not None else " (undefined: no losing trades or no trades)")))
    dd = oos.get("max_drawdown_pct")
    gates.append(_gate("max_drawdown", dd is not None and dd <= risk.max_drawdown_pct, dd,
                       f"<= {risk.max_drawdown_pct}%", "worst peak-to-trough fall of the equity curve"))
    n = oos.get("trades", 0)
    gates.append(_gate("sample_size", n >= cfg.min_trades, n, f">= {cfg.min_trades}",
                       "completed trades; trades are not independent, so even this is a thin sample"))
    stress_exp = stress.get("expectancy_r") if stress else None
    gates.append(_gate("cost_stress", stress_exp is not None and stress_exp > 0, stress_exp,
                       f"> 0 R at {cfg.stress_multiplier}x costs", "the edge must survive doubled trading costs"))
    gates.append(_gate("parameter_stability", stability is not None and stability >= cfg.min_parameter_stability,
                       stability, f">= {cfg.min_parameter_stability}",
                       "share of neighbouring parameter sets that stay profitable"))
    envs = oos.get("by_environment") or {}
    need = min_trades_per_environment or cfg.min_trades_per_environment
    good_envs = [k for k, v in envs.items() if v.get("trades", 0) >= need and (v.get("expectancy_r") or 0) > 0]
    gates.append(_gate("multiple_regimes", len(good_envs) >= cfg.min_profitable_environments, good_envs,
                       f">= {cfg.min_profitable_environments} environments with >= {need} trades",
                       "profitable across different higher-timeframe market environments"))
    gates.append(_gate("walk_forward_consistency", window_ratio is not None
                       and window_ratio >= cfg.min_profitable_window_ratio, window_ratio,
                       f">= {cfg.min_profitable_window_ratio}", "share of walk-forward windows that were profitable"))
    without_best = oos.get("net_without_best_trade")
    gates.append(_gate("not_one_trade", without_best is not None and without_best > 0, without_best, "> 0",
                       "net profit with the single best trade removed"))
    month_share = oos.get("best_month_share_pct")
    gates.append(_gate("not_one_period", month_share is not None and month_share <= cfg.max_best_month_share * 100,
                       month_share, f"<= {cfg.max_best_month_share * 100:.0f}%",
                       "share of the profit made in the single best month"))
    if cfg.require_ci_above_zero:
        lower = None
        if bootstrap and bootstrap.get("available"):
            lower = bootstrap["expectancy_r"]["ci"][0]
        gates.append(_gate("statistical_confidence", lower is not None and lower > 0, lower, "> 0",
                           "lower bound of the 95% bootstrap interval of expectancy (R)"))
    failed = [g["gate"] for g in gates if not g["passed"]]
    verdict = PASSED if not failed else NO_EDGE
    message = ("All preliminary gates passed. This is not proof of future profitability; it qualifies the strategy "
               "for a forward test on paper." if not failed else
               "The current system has not demonstrated a reliable edge: " + ", ".join(failed) + " failed.")
    return {"verdict": verdict, "passed": not failed, "failed": failed, "gates": gates, "message": message}
