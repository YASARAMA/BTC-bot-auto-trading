"""Health, version, settings, jobs and the critical-error latch."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app import __version__
from app.api.deps import services
from app.config import EDITABLE_SECTIONS
from app.core import limits
from app.core.version import software_version

router = APIRouter(prefix="/api", tags=["system"])


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, dict[str, Any]] = Field(..., description="section -> {key: value}")


class ClearCritical(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field("", max_length=500)


@router.get("/health")
def health(svc=Depends(services)) -> dict[str, Any]:
    return svc.health.snapshot()


@router.get("/version")
def version() -> dict[str, Any]:
    return {
        "name": "Claude Adaptive Trader",
        "version": __version__,
        "software_version": software_version(),
        "modes": list(limits.SUPPORTED_MODES),
        "real_money_execution": "not implemented in this version",
    }


def _hard_limits() -> dict[str, Any]:
    return {
        "risk_per_trade_pct": limits.HARD_MAX_RISK_PER_TRADE_PCT,
        "open_risk_pct": limits.HARD_MAX_OPEN_RISK_PCT,
        "position_pct": limits.HARD_MAX_POSITION_PCT,
        "total_exposure_pct": limits.HARD_MAX_TOTAL_EXPOSURE_PCT,
        "positions": limits.HARD_MAX_POSITIONS,
        "daily_loss_pct": limits.HARD_MAX_DAILY_LOSS_PCT,
        "drawdown_pct": limits.HARD_MAX_DRAWDOWN_PCT,
        "leverage": "not available",
        "short_selling": "not available",
        "live_trading": "not available",
    }


def _settings_payload(svc) -> dict[str, Any]:
    return {
        "effective": svc.settings.printable(),
        "file": svc.settings_store.base.printable(),
        "overrides": svc.settings_store.overrides(),
        "editable": {k: sorted(v) for k, v in EDITABLE_SECTIONS.items()},
        "hard_limits": _hard_limits(),
        "config_hash": svc.settings.config_hash(),
        "secrets": {"anthropic_api_key_present": svc.anthropic_key_present()},
    }


@router.get("/settings")
def get_settings(svc=Depends(services)) -> dict[str, Any]:
    return _settings_payload(svc)


@router.put("/settings")
def update_settings(body: SettingsUpdate, svc=Depends(services)) -> dict[str, Any]:
    svc.settings_store.update(body.changes, actor="user")
    return _settings_payload(svc)


@router.delete("/settings")
def reset_settings(section: str | None = None, svc=Depends(services)) -> dict[str, Any]:
    svc.settings_store.reset(section, actor="user")
    return _settings_payload(svc)


@router.get("/jobs")
def list_jobs(limit: int = 50, svc=Depends(services)) -> list[dict[str, Any]]:
    return [j.to_dict() for j in svc.jobs.list(min(max(limit, 1), 200))]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, svc=Depends(services)) -> dict[str, Any]:
    return svc.jobs.get(job_id).to_dict()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, svc=Depends(services)) -> dict[str, Any]:
    return svc.jobs.cancel(job_id).to_dict()


@router.get("/system/critical")
def get_critical(svc=Depends(services)) -> dict[str, Any]:
    return {"critical_error": svc.health.critical()}


@router.post("/system/critical/clear")
def clear_critical(body: ClearCritical, svc=Depends(services)) -> dict[str, Any]:
    svc.health.clear_critical("user", body.note)
    return {"critical_error": svc.health.critical()}
