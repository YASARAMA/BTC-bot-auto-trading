"""Structured logs and system diagnostics."""
from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter, Depends, Query

from app import __version__
from app.api.deps import services
from app.core.version import software_version
from app.monitoring.logging import flush_logs
from app.storage.database import from_json

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
def logs(level: str | None = None, source: str | None = None, q: str | None = None,
         before: int | None = None, limit: int = Query(200, ge=1, le=2000), svc=Depends(services)) -> dict[str, Any]:
    flush_logs()
    where, params = ["1 = 1"], []
    if level:
        levels = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        minimum = levels.get(level.upper(), 20)
        allowed = [k for k, v in levels.items() if v >= minimum]
        where.append("level IN (" + ",".join("?" for _ in allowed) + ")")
        params.extend(allowed)
    if source:
        where.append("source = ?")
        params.append(source)
    if q:
        where.append("message LIKE ?")
        params.append(f"%{q[:100]}%")
    if before:
        where.append("id < ?")
        params.append(before)
    rows = svc.db.query(f"SELECT * FROM logs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", [*params, limit])
    for r in rows:
        r["context"] = from_json(r["context"], None)
    sources = [r["source"] for r in svc.db.query("SELECT DISTINCT source FROM logs ORDER BY source")]
    counts = {r["level"]: r["n"] for r in svc.db.query("SELECT level, COUNT(*) AS n FROM logs GROUP BY level")}
    return {"rows": rows, "sources": sources, "counts": counts}


@router.get("/diagnostics")
def diagnostics(svc=Depends(services)) -> dict[str, Any]:
    s = svc.settings
    inventory = svc.candle_store.inventory()
    return {
        "health": svc.health.snapshot(),
        "versions": {"app": __version__, "software": software_version(), "python": sys.version.split()[0],
                     "platform": platform.platform()},
        "config_hash": s.config_hash(),
        "databases": {
            "state": {"path": svc.db.path, "bytes": svc.db.size_bytes(), "schema": svc.db.schema_version,
                      "integrity": svc.db.integrity_check()},
            "market": {"path": svc.market_db.path, "bytes": svc.market_db.size_bytes(),
                       "schema": svc.market_db.schema_version, "integrity": svc.market_db.integrity_check()},
        },
        "data": {"series": len(inventory), "candles": sum(r["candles"] for r in inventory),
                 "gappy_series": [f"{r['exchange']} {r['pair']} {r['timeframe']} ({r['missing']} missing)"
                                  for r in inventory if r["missing"]]},
        "market": svc.market.status(),
        "jobs": [j.to_dict() for j in svc.jobs.list(20)],
        "research": {
            "experiments": svc.db.scalar("SELECT COUNT(*) FROM experiments") or 0,
            "failed_experiments": svc.db.scalar("SELECT COUNT(*) FROM experiments WHERE status = 'failed'") or 0,
            "datasets": svc.db.scalar("SELECT COUNT(*) FROM datasets") or 0,
            "test_set_accesses": svc.db.scalar("SELECT COUNT(*) FROM test_set_access") or 0,
            "assistant": svc.researcher.status(),
        },
        "paper": svc.paper.status_lite(),
        "safety": {"real_money_execution": "not implemented", "leverage": "not available",
                   "short_selling": "not available", "exchange_credentials": "none used or stored"},
    }
