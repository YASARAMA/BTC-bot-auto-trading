"""Portfolio, positions, orders, trades and equity - from stored records only."""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from app.api.deps import services
from app.core.clock import iso, parse_ts
from app.core.errors import InvalidRequest

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

TRADE_COLUMNS = ["id", "source", "run_id", "scenario", "segment", "pair", "timeframe", "strategy", "strategy_version",
                 "regime", "environment", "entry_ts", "exit_ts", "entry_price", "exit_price", "qty", "stop_price",
                 "take_profit", "gross_pnl", "fees", "slippage_cost", "net_pnl", "return_pct", "initial_risk",
                 "r_multiple", "exit_reason", "entry_reason", "mae_pct", "mfe_pct", "bars_held"]


def trade_filters(source: str | None, run_id: str | None, scenario: str | None, pair: str | None,
                  strategy: str | None, regime: str | None, timeframe: str | None, start: str | None,
                  end: str | None) -> tuple[str, list[Any]]:
    where, params = ["1 = 1"], []
    for col, val in (("source", source), ("run_id", run_id), ("scenario", scenario), ("pair", pair),
                     ("strategy", strategy), ("regime", regime), ("timeframe", timeframe)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    try:
        if start:
            where.append("exit_ts >= ?")
            params.append(parse_ts(start))
        if end:
            where.append("exit_ts <= ?")
            params.append(parse_ts(end))
    except ValueError:
        raise InvalidRequest("start and end must be ISO dates or millisecond timestamps") from None
    return " AND ".join(where), params


def current_run(svc: Any, source: str | None, run_id: str | None) -> tuple[str | None, str | None]:
    if source in (None, "paper") and run_id is None and svc.paper.session:
        return "paper", svc.paper.session["id"]
    return source, run_id


@router.get("/summary")
def summary(svc=Depends(services)) -> dict[str, Any]:
    st = svc.paper.status()
    sid = (st.get("session") or {}).get("id")
    trades = svc.db.query("SELECT net_pnl, fees, exit_ts FROM trades WHERE source = 'paper' AND run_id = ?",
                          (sid,)) if sid else []
    return {"status": st["status"], "session": st.get("session"), "portfolio": st.get("portfolio"),
            "trades": len(trades), "label": st.get("label"),
            "note": "Simulated paper-trading values. They are not realised financial profit."}


@router.get("/positions")
def positions(session_id: str | None = None, status: str = "open", svc=Depends(services)) -> list[dict[str, Any]]:
    sid = session_id or (svc.paper.session or {}).get("id")
    if not sid:
        return []
    rows = svc.db.query("SELECT * FROM positions WHERE session_id = ? AND (? = 'all' OR status = ?)"
                        " ORDER BY entry_ts DESC", (sid, status, status))
    marks = svc.paper.core.marks if svc.paper.core else {}
    for r in rows:
        mark = marks.get(r["pair"])
        r["mark"] = mark
        if mark is not None and r["status"] == "open":
            remaining = r["qty"] - r["qty_exited"]
            r["unrealized_pnl"] = remaining * (mark - r["entry_price"]) - r["entry_fee"] * (remaining / r["qty"])
            r["unrealized_pct"] = (mark / r["entry_price"] - 1) * 100
    return rows


@router.get("/orders")
def orders(session_id: str | None = None, status: str | None = None, limit: int = Query(300, ge=1, le=5000),
           svc=Depends(services)) -> list[dict[str, Any]]:
    sid = session_id or (svc.paper.session or {}).get("id")
    if not sid:
        return []
    if status:
        return svc.db.query("SELECT * FROM orders WHERE session_id = ? AND status = ? ORDER BY created_ts DESC LIMIT ?",
                            (sid, status, limit))
    return svc.db.query("SELECT * FROM orders WHERE session_id = ? ORDER BY created_ts DESC, id DESC LIMIT ?",
                        (sid, limit))


@router.get("/trades")
def trades(source: str | None = None, run_id: str | None = None, scenario: str | None = None,
           pair: str | None = None, strategy: str | None = None, regime: str | None = None,
           timeframe: str | None = None, start: str | None = None, end: str | None = None,
           limit: int = Query(1000, ge=1, le=20000), svc=Depends(services)) -> list[dict[str, Any]]:
    source, run_id = current_run(svc, source, run_id)
    where, params = trade_filters(source, run_id, scenario, pair, strategy, regime, timeframe, start, end)
    return svc.db.query(f"SELECT * FROM trades WHERE {where} ORDER BY exit_ts DESC LIMIT ?", [*params, limit])


@router.get("/trades.csv", response_class=PlainTextResponse)
def trades_csv(source: str | None = None, run_id: str | None = None, scenario: str | None = None,
               pair: str | None = None, strategy: str | None = None, regime: str | None = None,
               timeframe: str | None = None, start: str | None = None, end: str | None = None,
               svc=Depends(services)) -> PlainTextResponse:
    source, run_id = current_run(svc, source, run_id)
    where, params = trade_filters(source, run_id, scenario, pair, strategy, regime, timeframe, start, end)
    rows = svc.db.query(f"SELECT * FROM trades WHERE {where} ORDER BY exit_ts", params)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["entry_time_utc", "exit_time_utc", *TRADE_COLUMNS])
    for r in rows:
        w.writerow([iso(r["entry_ts"]), iso(r["exit_ts"]), *[r.get(c) for c in TRADE_COLUMNS]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="trades.csv"'})


@router.get("/equity")
def equity(source: str | None = None, run_id: str | None = None, scenario: str = "base",
           max_points: int = Query(3000, ge=10, le=20000), svc=Depends(services)) -> dict[str, Any]:
    source, run_id = current_run(svc, source, run_id)
    if not run_id:
        return {"source": source, "run_id": None, "points": []}
    rows = svc.db.query("SELECT ts, equity, cash, exposure, open_risk, drawdown_pct, benchmark FROM equity_curve"
                        " WHERE source = ? AND run_id = ? AND scenario = ? ORDER BY ts", (source, run_id, scenario))
    if len(rows) > max_points:
        stride = len(rows) / max_points
        rows = [rows[int(i * stride)] for i in range(max_points - 1)] + [rows[-1]]
    return {"source": source, "run_id": run_id, "scenario": scenario, "points": rows}
