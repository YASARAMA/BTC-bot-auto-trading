"""Backtesting laboratory: run experiments as background jobs and read their stored results."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.deps import services
from app.backtesting import datasets
from app.backtesting.runner import BacktestRequest, FinalTestRequest, ValidationRequest
from app.core.errors import InvalidRequest, NotFound
from app.storage.database import from_json

router = APIRouter(prefix="/api", tags=["research"])


@router.post("/backtests")
def start_backtest(body: BacktestRequest, svc=Depends(services)) -> dict[str, Any]:
    job = svc.jobs.submit("backtest", f"Backtest {body.strategy} ({body.segment})",
                          lambda ctx: svc.research.backtest(body, ctx))
    return job.to_dict()


@router.post("/validations")
def start_validation(body: ValidationRequest, svc=Depends(services)) -> dict[str, Any]:
    job = svc.jobs.submit("validation", f"Validation protocol for {body.strategy}",
                          lambda ctx: svc.research.validate(body, ctx))
    return job.to_dict()


@router.post("/final-tests")
def start_final_test(body: FinalTestRequest, svc=Depends(services)) -> dict[str, Any]:
    job = svc.jobs.submit("final_test", f"Final test of version {body.version_id}",
                          lambda ctx: svc.research.final_test(body, ctx, actor="user"))
    return job.to_dict()


@router.get("/experiments")
def experiments(kind: str | None = None, limit: int = Query(200, ge=1, le=1000),
                svc=Depends(services)) -> list[dict[str, Any]]:
    return svc.tracker.list(kind=kind, limit=limit)


@router.get("/experiments/{eid}")
def experiment(eid: str, svc=Depends(services)) -> dict[str, Any]:
    exp = svc.tracker.get(eid)
    exp.pop("config_snapshot", None)
    exp["scenarios"] = [r["scenario"] for r in svc.db.query(
        "SELECT DISTINCT scenario FROM equity_curve WHERE source = 'backtest' AND run_id = ?", (eid,))]
    exp["files"] = sorted(p.name for p in _dir(svc, eid).glob("*")) if _dir(svc, eid).exists() else []
    return exp


@router.get("/experiments/{eid}/config")
def experiment_config(eid: str, svc=Depends(services)) -> dict[str, Any]:
    return {"id": eid, "config": svc.tracker.get(eid)["config_snapshot"]}


def _dir(svc: Any, eid: str) -> Path:
    if "/" in eid or ".." in eid:
        raise InvalidRequest("invalid experiment id")
    return svc.settings.reports_path / eid


@router.get("/experiments/{eid}/report.md", response_class=PlainTextResponse)
def report(eid: str, svc=Depends(services)) -> PlainTextResponse:
    path = _dir(svc, eid) / "report.md"
    if not path.exists():
        raise NotFound("this experiment has no report (it may have failed or still be running)")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{eid}-report.md"'})


@router.get("/experiments/{eid}/files/{name}")
def report_file(eid: str, name: str, svc=Depends(services)) -> FileResponse:
    if "/" in name or ".." in name or not name.endswith((".csv", ".json", ".md")):
        raise InvalidRequest("invalid file name")
    path = _dir(svc, eid) / name
    if not path.exists():
        raise NotFound(f"{name} not found")
    return FileResponse(path, filename=f"{eid}-{name}")


@router.get("/experiments/{eid}/equity")
def experiment_equity(eid: str, scenario: str = "base", svc=Depends(services)) -> dict[str, Any]:
    rows = svc.db.query("SELECT ts, equity, cash, exposure, open_risk, drawdown_pct, benchmark FROM equity_curve"
                        " WHERE source = 'backtest' AND run_id = ? AND scenario = ? ORDER BY ts", (eid, scenario))
    return {"run_id": eid, "scenario": scenario, "points": rows}


@router.get("/experiments/{eid}/trades")
def experiment_trades(eid: str, scenario: str = "base", limit: int = Query(2000, ge=1, le=20000),
                      svc=Depends(services)) -> list[dict[str, Any]]:
    return svc.db.query("SELECT * FROM trades WHERE source = 'backtest' AND run_id = ? AND scenario = ?"
                        " ORDER BY entry_ts LIMIT ?", (eid, scenario, limit))


@router.get("/datasets")
def list_datasets(svc=Depends(services)) -> list[dict[str, Any]]:
    out = []
    for ds in datasets.all_datasets(svc.db):
        d = ds.to_dict()
        d["test_accesses"] = svc.db.query("SELECT ts, experiment_id, strategies, reason, actor FROM test_set_access"
                                          " WHERE dataset_id = ? ORDER BY ts", (ds.id,))
        for a in d["test_accesses"]:
            a["strategies"] = from_json(a["strategies"], [])
        out.append(d)
    return out
