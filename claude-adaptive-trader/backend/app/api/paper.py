"""Paper trading control and its audit trail."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import services
from app.core.timeframes import floor_ts, validate_timeframe
from app.execution.paper_trader import PaperStart
from app.storage.database import from_json

router = APIRouter(prefix="/api/paper", tags=["paper"])


class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field("", max_length=500)


class KillSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool
    reason: str = Field("", max_length=500)


@router.get("/status")
def status(svc=Depends(services)) -> dict[str, Any]:
    return svc.paper.status()


@router.get("/status-lite")
def status_lite(svc=Depends(services)) -> dict[str, Any]:
    return svc.paper.status_lite()


@router.post("/start")
def start(body: PaperStart, svc=Depends(services)) -> dict[str, Any]:
    return svc.paper.start(body)


@router.post("/pause")
def pause(svc=Depends(services)) -> dict[str, Any]:
    return svc.paper.pause()


@router.post("/resume")
def resume(svc=Depends(services)) -> dict[str, Any]:
    return svc.paper.resume()


@router.post("/stop")
def stop(svc=Depends(services)) -> dict[str, Any]:
    return svc.paper.stop()


@router.post("/kill-switch")
def kill_switch(body: KillSwitch, svc=Depends(services)) -> dict[str, Any]:
    return svc.paper.set_kill_switch(body.on, actor="user", reason=body.reason or ("on" if body.on else "off"))


@router.post("/close-all")
def close_all(body: Reason, svc=Depends(services)) -> dict[str, Any]:
    return svc.paper.close_all(body.reason or "requested from the dashboard")


@router.get("/sessions")
def sessions(svc=Depends(services)) -> list[dict[str, Any]]:
    rows = svc.db.query(
        "SELECT id, created_at, updated_at, status, data_mode, strategy_mode, forward_strategy, pairs, timeframe,"
        " exchange, initial_cash, cash, started_at, stopped_at, last_error, note FROM paper_sessions"
        " ORDER BY created_at DESC LIMIT 100")
    for r in rows:
        r["pairs"] = from_json(r["pairs"], [])
    return rows


@router.get("/decisions")
def decisions(session_id: str | None = None, pair: str | None = None, action: str | None = None,
              accepted: bool | None = None, limit: int = Query(200, ge=1, le=2000),
              svc=Depends(services)) -> list[dict[str, Any]]:
    sid = session_id or (svc.paper.session or {}).get("id")
    if not sid:
        return []
    where, params = ["session_id = ?"], [sid]
    if pair:
        where.append("pair = ?")
        params.append(pair)
    if action:
        where.append("action = ?")
        params.append(action)
    if accepted is not None:
        where.append("accepted = ?")
        params.append(1 if accepted else 0)
    rows = svc.db.query(f"SELECT * FROM decisions WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC LIMIT ?",
                        [*params, limit])
    for r in rows:
        r["checks"] = from_json(r["checks"], None)
        r["signal"] = from_json(r["signal"], None)
        r["accepted"] = bool(r["accepted"])
    return rows


@router.get("/chart-annotations")
def chart_annotations(pair: str, timeframe: str, session_id: str | None = None,
                      svc=Depends(services)) -> dict[str, Any]:
    validate_timeframe(timeframe)
    sid = session_id or (svc.paper.session or {}).get("id")
    if not sid:
        return {"markers": [], "levels": [], "session_id": None}
    markers: list[dict[str, Any]] = []
    for o in svc.db.query("SELECT * FROM orders WHERE session_id = ? AND pair = ? AND filled_ts IS NOT NULL"
                          " ORDER BY filled_ts LIMIT 2000", (sid, pair)):
        t = floor_ts(int(o["filled_ts"]), timeframe) // 1000
        if o["side"] == "buy":
            markers.append({"time": t, "position": "belowBar", "shape": "arrowUp", "color": "#2ebd85",
                            "text": f"BUY {o['fill_price']:.6g}"})
        else:
            markers.append({"time": t, "position": "aboveBar", "shape": "arrowDown", "color": "#f6465d",
                            "text": f"SELL {o['fill_price']:.6g}"})
    for d in svc.db.query("SELECT ts, accepted, strategy FROM decisions WHERE session_id = ? AND pair = ?"
                          " AND action = 'enter' ORDER BY ts DESC LIMIT 500", (sid, pair)):
        # A signal is known at the close of its candle; it is drawn on that (closed) candle.
        signal_open = int(d["ts"]) - 1
        t = floor_ts(signal_open, timeframe) // 1000
        markers.append({"time": t, "position": "aboveBar", "shape": "circle",
                        "color": "#7c8cff" if d["accepted"] else "#8f99ab",
                        "text": "signal" if d["accepted"] else "rejected"})
    levels = []
    for p in svc.db.query("SELECT * FROM positions WHERE session_id = ? AND pair = ? AND status = 'open'", (sid, pair)):
        levels.append({"price": p["entry_price"], "label": "entry", "kind": "entry"})
        levels.append({"price": p["stop_price"], "label": "stop", "kind": "stop"})
        if p["take_profit"]:
            levels.append({"price": p["take_profit"], "label": "target", "kind": "target"})
    return {"markers": markers, "levels": levels, "session_id": sid}
