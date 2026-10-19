"""Everything the overview dashboard shows, from stored state only."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import services
from app.api.strategies import current_regimes
from app.backtesting.metrics import compute_metrics
from app.core.clock import now_ms, utc_day_start

router = APIRouter(prefix="/api", tags=["dashboard"])

DAY = 86_400_000


def _value_at(points: list[dict[str, Any]], ts: int) -> float | None:
    """Equity at the last point at or before ``ts``."""
    prev = None
    for p in points:
        if p["ts"] > ts:
            break
        prev = p["equity"]
    return prev


def _downsample(points: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """At most ``n`` evenly spaced points, always keeping the last one."""
    if len(points) <= n:
        return points
    stride = len(points) / n
    return [points[int(i * stride)] for i in range(n - 1)] + [points[-1]]


def _ret(now: float, then: float | None) -> float | None:
    return round((now / then - 1) * 100, 4) if then else None


@router.get("/dashboard")
def dashboard(svc=Depends(services)) -> dict[str, Any]:
    st = svc.paper.status()
    session = st.get("session")
    sid = (session or {}).get("id")
    pf = st.get("portfolio")
    points = svc.db.query("SELECT ts, equity, cash, exposure, open_risk, drawdown_pct FROM equity_curve"
                          " WHERE source = 'paper' AND run_id = ? ORDER BY ts", (sid,)) if sid else []
    trades = svc.db.query("SELECT * FROM trades WHERE source = 'paper' AND run_id = ? ORDER BY exit_ts", (sid,)) \
        if sid else []
    perf = None
    returns = {}
    if session and pf:
        equity = pf["equity"]
        initial = session["initial_cash"]
        clock = session.get("clock_ts") or now_ms()
        perf = compute_metrics(trades, points, initial_cash=initial)
        for k in ("monthly_returns_pct", "by_regime", "by_pair", "by_strategy", "by_exit_reason", "by_environment"):
            perf.pop(k, None)
        day_start = utc_day_start(clock)
        returns = {
            "daily_pct": _ret(equity, _value_at(points, day_start) or initial),
            "weekly_pct": _ret(equity, _value_at(points, clock - 7 * DAY) or initial),
            "monthly_pct": _ret(equity, _value_at(points, clock - 30 * DAY) or initial),
            "since_start_pct": _ret(equity, initial),
            "basis": "replay clock" if session["data_mode"] == "replay" else "wall clock",
        }
    selector_state = {r["pair"]: r for r in svc.db.query("SELECT * FROM selector_state WHERE session_id = ?",
                                                           (sid,))} if sid else {}
    active = {}
    if session:
        for pair in session["pairs"]:
            if session["strategy_mode"] == "forward_test":
                name = session["forward_strategy"]
            else:
                name = (selector_state.get(pair) or {}).get("active_strategy")
            strat = svc.paper.strategies.get(name) if name else None
            active[pair] = {"strategy": name, "version": strat.version if strat else None,
                            "mode": session["strategy_mode"]}
    events = svc.db.query("SELECT ts, pair, action, accepted, reason, strategy, regime FROM decisions"
                          " WHERE session_id = ? AND action IN ('enter', 'exit') ORDER BY ts DESC, id DESC LIMIT 12",
                          (sid,)) if sid else []
    for e in events:
        e["accepted"] = bool(e["accepted"])
    orders = svc.db.query("SELECT client_order_id, pair, side, purpose, status, fill_price, filled_qty, created_ts,"
                          " filled_ts, reason, reject_reason FROM orders WHERE session_id = ?"
                          " ORDER BY COALESCE(filled_ts, created_ts) DESC LIMIT 12", (sid,)) if sid else []
    market = svc.market.status()
    s = svc.settings
    br = st.get("breakers") or {}
    equity_now = (pf or {}).get("equity") or s.portfolio.initial_cash
    return {
        "bot": {"status": st["status"], "label": st.get("label"), "session": session, "engine": st.get("engine"),
                "kill_switch": st.get("kill_switch"), "last_error": st.get("last_error")},
        "portfolio": pf,
        "returns": returns,
        "performance": perf,
        "positions": st.get("positions") or [],
        "recent_trades": list(reversed(trades[-10:])),
        "equity": _downsample(points, 600),
        "engine_pairs": st.get("pairs") or {},
        "regimes": current_regimes(svc),
        "active_strategies": active,
        "risk": {
            "open_risk_pct": ((pf or {}).get("open_risk", 0.0) / equity_now * 100) if equity_now else 0.0,
            "open_risk_limit_pct": s.risk.max_open_risk_pct,
            "exposure_pct": (pf or {}).get("exposure_pct", 0.0),
            "exposure_limit_pct": s.risk.max_total_exposure_pct,
            "daily_loss_pct": max(0.0, br.get("daily_loss_pct") or 0.0),
            "daily_loss_limit_pct": s.risk.daily_loss_limit_pct,
            "drawdown_pct": max(0.0, br.get("drawdown_pct") or 0.0),
            "drawdown_limit_pct": s.risk.max_drawdown_pct,
            "daily_tripped": br.get("daily_tripped", False), "drawdown_tripped": br.get("drawdown_tripped", False),
        },
        "market": {"status": market["status"], "websocket": market["websocket"].get("connected"),
                   "rest_reachable": market["rest"].get("reachable"), "rest_error": market["rest"].get("last_error"),
                   "series": market["series"], "tickers": svc.market.tickers()},
        "events": events,
        "orders": orders,
        "notice": "Portfolio values are simulated paper-trading results, not realised money. Market prices are "
                  "shown with their source and age.",
    }
