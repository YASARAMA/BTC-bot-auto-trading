"""The optional Claude research assistant, research notes and human decisions on proposals."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import services
from app.backtesting.runner import SelectorRequest, ValidationRequest

router = APIRouter(prefix="/api", tags=["research"])


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str


class Explain(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: str
    b: str


class LogReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: int = Field(24, ge=1, le=24 * 30)


class Propose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: str
    count: int = Field(3, ge=1, le=5)
    experiment_ids: list[str] | None = None


class UserNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=300)
    body: str = Field(..., min_length=1, max_length=20_000)
    experiment_id: str | None = None


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(..., min_length=3, max_length=1000)
    run_validation: bool = False


@router.get("/research/status")
def status(svc=Depends(services)) -> dict[str, Any]:
    return svc.researcher.status()


@router.get("/research/notes")
def notes(status: str | None = None, kind: str | None = None, limit: int = Query(200, ge=1, le=1000),
          svc=Depends(services)) -> list[dict[str, Any]]:
    return svc.researcher.notes(status=status, kind=kind, limit=limit)


@router.post("/research/notes")
def add_note(body: UserNote, svc=Depends(services)) -> dict[str, Any]:
    return svc.researcher.add_user_note(body.title, body.body, body.experiment_id)


@router.post("/research/review")
def review(body: Review, svc=Depends(services)) -> dict[str, Any]:
    svc.researcher.status()
    return svc.jobs.submit("assistant", f"Assistant review of {body.experiment_id}",
                           lambda ctx: svc.researcher.review_experiment(body.experiment_id)).to_dict()


@router.post("/research/explain")
def explain(body: Explain, svc=Depends(services)) -> dict[str, Any]:
    return svc.jobs.submit("assistant", f"Assistant comparison {body.a} vs {body.b}",
                           lambda ctx: svc.researcher.explain_difference(body.a, body.b)).to_dict()


@router.post("/research/review-logs")
def review_logs(body: LogReview, svc=Depends(services)) -> dict[str, Any]:
    return svc.jobs.submit("assistant", "Assistant log review",
                           lambda ctx: svc.researcher.review_logs(body.hours)).to_dict()


@router.post("/research/propose")
def propose(body: Propose, svc=Depends(services)) -> dict[str, Any]:
    return svc.jobs.submit("assistant", f"Assistant proposals for {body.strategy}",
                           lambda ctx: svc.researcher.propose_experiments(
                               body.strategy, count=body.count, experiment_ids=body.experiment_ids)).to_dict()


@router.post("/research/notes/{note_id}/approve")
def approve(note_id: int, body: Decision, svc=Depends(services)) -> dict[str, Any]:
    out = svc.researcher.decide(note_id, approve=True, actor="user", note=body.note)
    if body.run_validation and out.get("version"):
        v = out["version"]
        job = svc.jobs.submit("validation", f"Validation protocol for {v['version']}",
                              lambda ctx: svc.research.validate(
                                  ValidationRequest(strategy=v["strategy"], params=v["params"],
                                                    hypothesis=v.get("hypothesis")), ctx))
        out["validation_job"] = job.to_dict()
    return out


@router.post("/research/notes/{note_id}/reject")
def reject(note_id: int, body: Decision, svc=Depends(services)) -> dict[str, Any]:
    return svc.researcher.decide(note_id, approve=False, actor="user", note=body.note)


@router.post("/selector-validations")
def selector_validation(body: SelectorRequest, svc=Depends(services)) -> dict[str, Any]:
    return svc.jobs.submit("selector_walk_forward", "Adaptive selector walk-forward",
                           lambda ctx: svc.research.selector_validation(body, ctx)).to_dict()
