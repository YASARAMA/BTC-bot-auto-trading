"""Strategies, versions, approvals, the adaptive selector and current regimes."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import services
from app.core.errors import NotFound
from app.core.timeframes import last_closed_open, tf_ms
from app.regimes.detector import explain
from app.research.adaptive import AdaptiveSelector, evidence_from_db
from app.strategies.pipeline import prepare
from app.strategies.registry import STRATEGIES, build

router = APIRouter(prefix="/api", tags=["strategies"])


class NewVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    params: dict[str, Any] = Field(default_factory=dict)
    hypothesis: str | None = Field(None, max_length=2000)


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(..., min_length=3, max_length=1000)
    override: bool = False


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    note: str = Field(..., min_length=3, max_length=1000)


def _strategy_payload(svc: Any, name: str) -> dict[str, Any]:
    default = build(name)
    versions = svc.tracker.versions(name)
    approved = next((v for v in versions if v["status"] == "approved"), None)
    latest_validated = next((v for v in versions if v.get("validation")), None)
    shown = approved or latest_validated
    evidence = None
    if shown:
        strat = build(name, shown["params"])
        evidence = evidence_from_db(svc.db, {name: strat}, {name: shown}, svc.settings.selector).get(name)
    return {
        "name": name, "title": default.title, "spec": default.spec(), "versions": versions,
        "approved": approved, "evidence": evidence.to_dict() if evidence else None,
        "evidence_version": shown["version"] if shown else None,
    }


@router.get("/strategies")
def strategies(svc=Depends(services)) -> list[dict[str, Any]]:
    return [_strategy_payload(svc, name) for name in STRATEGIES]


@router.get("/strategies/{name}")
def strategy(name: str, svc=Depends(services)) -> dict[str, Any]:
    if name not in STRATEGIES:
        raise NotFound(f"unknown strategy {name}")
    return _strategy_payload(svc, name)


@router.post("/strategies/{name}/versions")
def new_version(name: str, body: NewVersion, svc=Depends(services)) -> dict[str, Any]:
    if name not in STRATEGIES:
        raise NotFound(f"unknown strategy {name}")
    return svc.tracker.ensure_version(name, body.params, created_by="user", hypothesis=body.hypothesis)


@router.post("/strategies/versions/{version_id}/approve")
def approve(version_id: int, body: Approval, svc=Depends(services)) -> dict[str, Any]:
    return svc.tracker.approve(version_id, actor="user", note=body.note, override=body.override)


@router.post("/strategies/versions/{version_id}/status")
def set_status(version_id: int, body: StatusChange, svc=Depends(services)) -> dict[str, Any]:
    return svc.tracker.set_status(version_id, body.status, actor="user", note=body.note)


@router.get("/selector")
def selector(svc=Depends(services)) -> dict[str, Any]:
    s = svc.settings
    approved = svc.tracker.approved()
    strats = {n: build(n, approved[n]["params"]) for n in approved}
    evidence = evidence_from_db(svc.db, strats, approved, s.selector)
    sel = AdaptiveSelector(evidence, s.selector, require_approved=True)
    session = svc.paper.session
    states = {}
    if session:
        for r in svc.db.query("SELECT * FROM selector_state WHERE session_id = ?", (session["id"],)):
            states[r["pair"]] = r
    pairs = {}
    regimes = current_regimes(svc)
    for pair, info in regimes["pairs"].items():
        regime = info.get("regime") or "uncertain"
        pairs[pair] = {"regime": regime, "table": sel.table(regime) if regime != "uncertain" else [],
                       "state": states.get(pair)}
    return {"approved": {k: v["version"] for k, v in approved.items()},
            "evidence": {k: v.to_dict() for k, v in evidence.items()}, "pairs": pairs,
            "config": s.selector.model_dump(mode="json"),
            "note": "Only approved versions with enough out-of-sample evidence in the current regime are eligible. "
                    "With none eligible, the selector abstains (no trade)."}


_REGIME_CACHE: dict[str, Any] = {"ts": 0.0, "value": None}


def current_regimes(svc: Any) -> dict[str, Any]:
    """Regime of each configured pair on its latest stored closed candle (cached for 30 s)."""
    if _REGIME_CACHE["value"] is not None and time.time() - _REGIME_CACHE["ts"] < 30:
        return _REGIME_CACHE["value"]
    s = svc.settings
    tf = s.exchange.signal_timeframe
    out: dict[str, Any] = {}
    for pair in s.exchange.pairs:
        candles = svc.candle_store.load("binance", pair, tf, limit=3000, newest=True)
        if candles.empty:
            out[pair] = {"available": False, "reason": f"no {tf} candles stored for {pair}"}
            continue
        view = prepare(candles, pair, tf, list(s.exchange.confirm_timeframes), s)
        i = len(view.features) - 1
        row = view.regime.iloc[i]
        last = int(candles["ts"].iloc[-1])
        stale = last < last_closed_open(int(time.time() * 1000), tf) - tf_ms(tf)
        out[pair] = {"available": True, "candle_ts": last, "stale": stale, "regime": str(row["regime"]),
                     "confidence": float(row["regime_confidence"]), "reason": str(row["regime_reason"]),
                     "direction": int(row["regime_direction"]),
                     "explain": explain(view.features, i, s.regime, list(s.exchange.confirm_timeframes)),
                     "history": [{"ts": int(t), "regime": str(r)} for t, r in
                                 zip(view.features["ts"].iloc[-96:], view.regime["regime"].iloc[-96:])]}
    value = {"timeframe": tf, "pairs": out}
    _REGIME_CACHE.update({"ts": time.time(), "value": value})
    return value


@router.get("/regimes")
def regimes(svc=Depends(services)) -> dict[str, Any]:
    return current_regimes(svc)
