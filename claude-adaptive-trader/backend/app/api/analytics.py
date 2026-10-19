"""Performance analytics from stored results, with filters.

Sources: a paper session (``source=paper``) or a backtest experiment (``source=backtest`` with
its scenario). Filters narrow the trades by pair, strategy, regime, timeframe and period;
the equity-based views (equity, drawdown, exposure, monthly heatmap, benchmark) always
describe the whole run, which is stated in the response.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, Query

from app.api.deps import services
from app.api.portfolio import trade_filters
from app.backtesting.metrics import compute_metrics
from app.core.clock import utc_day, utc_month
from app.core.errors import InvalidRequest

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/runs")
def runs(svc=Depends(services)) -> list[dict[str, Any]]:
    out = []
    for s in svc.db.query("SELECT id, created_at, status, data_mode, strategy_mode, forward_strategy, timeframe,"
                          " pairs FROM paper_sessions ORDER BY created_at DESC LIMIT 50"):
        label = (f"Paper {s['data_mode']} · {s['strategy_mode']}"
                 + (f" ({s['forward_strategy']})" if s["forward_strategy"] else "") + f" · {s['id']}")
        out.append({"source": "paper", "run_id": s["id"], "label": label, "created_at": s["created_at"],
                    "timeframe": s["timeframe"], "scenarios": ["base"]})
    for e in svc.tracker.list(limit=100):
        if e["status"] != "completed":
            continue
        scenarios = [r["scenario"] for r in svc.db.query(
            "SELECT DISTINCT scenario FROM trades WHERE source = 'backtest' AND run_id = ?", (e["id"],))]
        if not scenarios:
            scenarios = [r["scenario"] for r in svc.db.query(
                "SELECT DISTINCT scenario FROM equity_curve WHERE source = 'backtest' AND run_id = ?", (e["id"],))]
        out.append({"source": "backtest", "run_id": e["id"], "label": f"{e['kind']} · {e.get('label') or e['id']}",
                    "created_at": e["created_at"], "timeframe": e["timeframe"], "scenarios": scenarios,
                    "uses_test_set": e["uses_test_set"]})
    return out


def _histogram(values: list[float], bins: int = 20) -> list[dict[str, Any]]:
    if not values:
        return []
    arr = np.array(values, dtype=float)
    lo, hi = float(np.nanmin(arr)), float(np.nanmax(arr))
    if lo == hi:
        return [{"from": lo, "to": hi, "count": len(values)}]
    counts, edges = np.histogram(arr, bins=bins, range=(lo, hi))
    return [{"from": round(float(edges[i]), 4), "to": round(float(edges[i + 1]), 4), "count": int(c)}
            for i, c in enumerate(counts)]


@router.get("")
def analytics(source: str = "paper", run_id: str | None = None, scenario: str = "base", pair: str | None = None,
              strategy: str | None = None, regime: str | None = None, timeframe: str | None = None,
              start: str | None = None, end: str | None = None, max_points: int = Query(2000, ge=10, le=10000),
              svc=Depends(services)) -> dict[str, Any]:
    if source not in {"paper", "backtest"}:
        raise InvalidRequest("source must be paper or backtest")
    if run_id is None:
        if source == "paper" and svc.paper.session:
            run_id = svc.paper.session["id"]
        else:
            return {"source": source, "run_id": None, "empty": True,
                    "message": "No run selected. Start a paper session or run a backtest."}
    if source == "paper":
        initial = svc.db.scalar("SELECT initial_cash FROM paper_sessions WHERE id = ?", (run_id,))
    else:
        exp = svc.tracker.get(run_id)
        initial = (exp.get("config_snapshot") or {}).get("portfolio", {}).get("initial_cash")
    initial = float(initial or svc.settings.portfolio.initial_cash)
    where, params = trade_filters(source, run_id, scenario, pair, strategy, regime, timeframe, start, end)
    trades = svc.db.query(f"SELECT * FROM trades WHERE {where} ORDER BY exit_ts", params)
    points = svc.db.query("SELECT ts, equity, cash, exposure, open_risk, drawdown_pct, benchmark FROM equity_curve"
                          " WHERE source = ? AND run_id = ? AND scenario = ? ORDER BY ts", (source, run_id, scenario))
    if len(points) > max_points:
        stride = len(points) / max_points
        points = [points[int(i * stride)] for i in range(max_points - 1)] + [points[-1]]
    filtered = any([pair, strategy, regime, timeframe, start, end])
    bench = [p["benchmark"] for p in points] if points and points[-1].get("benchmark") is not None else None
    m = compute_metrics(trades, points, initial_cash=initial, benchmark=bench)
    peak = -float("inf")
    drawdown = []
    for p in points:
        peak = max(peak, p["equity"])
        drawdown.append({"ts": p["ts"], "drawdown_pct": round((1 - p["equity"] / peak) * 100, 4) if peak > 0 else 0})
    daily: dict[str, float] = defaultdict(float)
    fees_by_month: dict[str, float] = defaultdict(float)
    for t in trades:
        daily[utc_day(t["exit_ts"])] += t["net_pnl"]
        fees_by_month[utc_month(t["exit_ts"])] += t["fees"]
    gross = sum(t["gross_pnl"] for t in trades)
    fees = sum(t["fees"] for t in trades)
    sensitivity = oos_vs_is = None
    if source == "backtest":
        exp = svc.tracker.get(run_id)
        summary = exp.get("summary") or {}
        sensitivity = summary.get("sensitivity")
        metrics = exp.get("metrics") or {}
        if "walk_forward_oos" in metrics:
            keys = ("trades", "net_return_pct", "expectancy_r", "profit_factor", "max_drawdown_pct")
            oos = metrics["walk_forward_oos"]
            oos_vs_is = {
                "in_sample_development": (sensitivity or {}).get("base"),
                "out_of_sample_walk_forward": {k: oos.get(k) for k in keys},
                "validation_segment": {k: (metrics.get("validation_base") or {}).get(k) for k in keys},
            }
    return {
        "source": source, "run_id": run_id, "scenario": scenario, "empty": not trades and not points,
        "filters": {"pair": pair, "strategy": strategy, "regime": regime, "timeframe": timeframe, "start": start,
                    "end": end},
        "equity_note": ("Equity, drawdown, exposure and the monthly heatmap describe the whole run; trade statistics "
                        "below follow the filters." if filtered else None),
        "metrics": m,
        "equity": points,
        "drawdown": drawdown,
        "exposure": [{"ts": p["ts"], "exposure_pct": round(p["exposure"] / p["equity"] * 100, 3) if p["equity"] else 0}
                     for p in points],
        "daily_pnl": [{"day": k, "pnl": round(v, 4)} for k, v in sorted(daily.items())],
        "monthly_returns": m.get("monthly_returns_pct") or {},
        "r_distribution": _histogram([t["r_multiple"] for t in trades if t["r_multiple"] is not None]),
        "pnl_distribution": _histogram([t["net_pnl"] for t in trades]),
        "wins": len([t for t in trades if t["net_pnl"] > 0]),
        "losses": len([t for t in trades if t["net_pnl"] <= 0]),
        "fee_contribution": {"gross_pnl": round(gross, 4), "fees": round(fees, 4), "net_pnl": round(gross - fees, 4),
                             "fees_by_month": {k: round(v, 4) for k, v in sorted(fees_by_month.items())}},
        "by_strategy": m.get("by_strategy"), "by_regime": m.get("by_regime"), "by_pair": m.get("by_pair"),
        "by_exit_reason": m.get("by_exit_reason"),
        "benchmark": [{"ts": p["ts"], "strategy": p["equity"], "benchmark": p["benchmark"]} for p in points]
        if bench else None,
        "sensitivity": sensitivity,
        "oos_vs_is": oos_vs_is,
        "options": {
            "pairs": sorted({t["pair"] for t in trades} | ({pair} if pair else set())),
            "strategies": sorted({t["strategy"] for t in trades} | ({strategy} if strategy else set())),
            "regimes": sorted({t["regime"] for t in trades} | ({regime} if regime else set())),
            "timeframes": sorted({t["timeframe"] for t in trades}),
        },
    }
