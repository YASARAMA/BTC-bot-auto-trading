"""Risk limits, utilisation, circuit breakers and recent pre-trade checks."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import services
from app.core import limits
from app.risk.risk_manager import CHECKS
from app.storage.database import from_json

router = APIRouter(prefix="/api/risk", tags=["risk"])


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(..., min_length=3, max_length=500)


@router.get("/status")
def status(svc=Depends(services)) -> dict[str, Any]:
    s = svc.settings
    st = svc.paper.status()
    pf = st.get("portfolio") or {}
    equity = pf.get("equity") or s.portfolio.initial_cash
    br = st.get("breakers") or {}
    open_risk = pf.get("open_risk", 0.0)
    return {
        "limits": s.risk.model_dump(mode="json"),
        "hard_limits": {"risk_per_trade_pct": limits.HARD_MAX_RISK_PER_TRADE_PCT,
                        "open_risk_pct": limits.HARD_MAX_OPEN_RISK_PCT,
                        "daily_loss_pct": limits.HARD_MAX_DAILY_LOSS_PCT,
                        "drawdown_pct": limits.HARD_MAX_DRAWDOWN_PCT},
        "checks": list(CHECKS),
        "utilization": {
            "equity": equity,
            "open_risk": open_risk,
            "open_risk_pct": open_risk / equity * 100 if equity else 0.0,
            "open_risk_limit_pct": s.risk.max_open_risk_pct,
            "exposure_pct": pf.get("exposure_pct", 0.0),
            "exposure_limit_pct": s.risk.max_total_exposure_pct,
            "positions": pf.get("positions", 0),
            "positions_limit": s.risk.max_positions,
            "daily_loss_pct": max(0.0, br.get("daily_loss_pct", 0.0) or 0.0),
            "daily_loss_limit_pct": s.risk.daily_loss_limit_pct,
            "drawdown_pct": max(0.0, br.get("drawdown_pct", 0.0) or 0.0),
            "drawdown_limit_pct": s.risk.max_drawdown_pct,
        },
        "breakers": br,
        "kill_switch": st.get("kill_switch"),
        "engine": st.get("engine"),
        "session": st.get("session"),
        "stop_note": "A stop-loss limits the planned loss but does not guarantee the exit price: prices can gap "
                     "through a stop, and the simulation fills such a stop at the worse opening price.",
    }


@router.get("/decisions")
def decisions(limit: int = Query(100, ge=1, le=1000), only_rejected: bool = False,
              svc=Depends(services)) -> list[dict[str, Any]]:
    sid = (svc.paper.session or {}).get("id")
    if not sid:
        return []
    extra = " AND accepted = 0" if only_rejected else ""
    rows = svc.db.query(f"SELECT * FROM decisions WHERE session_id = ? AND action = 'enter'{extra}"
                        " ORDER BY ts DESC, id DESC LIMIT ?", (sid, limit))
    for r in rows:
        r["checks"] = from_json(r["checks"], [])
        r["signal"] = from_json(r["signal"], None)
        r["accepted"] = bool(r["accepted"])
    return rows


@router.post("/breakers/reset-drawdown")
def reset_drawdown(body: Note, svc=Depends(services)) -> dict[str, Any]:
    return svc.paper.reset_drawdown(body.note)
