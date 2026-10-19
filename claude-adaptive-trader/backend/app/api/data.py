"""Historical data management: inventory, downloads, imports and gap reports."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import services
from app.core.errors import InvalidRequest
from app.core.timeframes import validate_timeframe
from app.data.importer import MAX_IMPORT_BYTES, import_csv, import_repo_samples

router = APIRouter(prefix="/api/data", tags=["data"])


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pairs: list[str] | None = None
    timeframes: list[str] | None = None
    days: int | None = Field(None, ge=1, le=3650)


@router.get("/inventory")
def inventory(svc=Depends(services)) -> dict[str, Any]:
    s = svc.settings
    inv = svc.candle_store.inventory()
    have = {(r["pair"], r["timeframe"]) for r in inv if r["exchange"] == "binance"}
    needed = [{"pair": p, "timeframe": tf} for p in s.exchange.pairs
              for tf in [s.exchange.signal_timeframe, *s.exchange.confirm_timeframes] if (p, tf) not in have]
    return {"series": inv, "missing_for_strategies": needed}


@router.post("/sync")
def sync(body: SyncRequest, svc=Depends(services)) -> dict[str, Any]:
    s = svc.settings
    pairs = body.pairs or s.exchange.pairs
    tfs = body.timeframes or [s.exchange.signal_timeframe, *s.exchange.confirm_timeframes]
    for tf in tfs:
        validate_timeframe(tf)
    for p in pairs:
        if p not in s.exchange.all_pairs:
            raise InvalidRequest(f"{p} is not a configured pair; add it in Settings first")
    job = svc.jobs.submit("download", f"Download {', '.join(pairs)} {', '.join(tfs)}",
                          lambda ctx: svc.market.sync_many(pairs, tfs, days=body.days, ctx=ctx))
    return job.to_dict()


@router.post("/import")
async def import_file(file: UploadFile = File(...), exchange: str = Form(...), pair: str = Form(...),
                      timeframe: str = Form(...), svc=Depends(services)) -> dict[str, Any]:
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise InvalidRequest("file is larger than the 300 MB import limit")
    return import_csv(svc.candle_store, content, filename=file.filename or "upload.csv", exchange=exchange,
                      pair=pair.upper(), timeframe=timeframe)


@router.post("/import-samples")
def import_samples(svc=Depends(services)) -> dict[str, Any]:
    return {"results": import_repo_samples(svc.candle_store)}


@router.get("/gaps")
def gaps(exchange: str, pair: str, timeframe: str, svc=Depends(services)) -> dict[str, Any]:
    missing, gap_list = svc.candle_store.gaps(exchange, pair, timeframe)
    return {"exchange": exchange, "pair": pair, "timeframe": timeframe, "missing": missing,
            "gaps": [list(g) for g in gap_list[:500]], "gap_count": len(gap_list)}


@router.delete("/series")
def delete_series(exchange: str, pair: str, timeframe: str, confirm: bool = False,
                  svc=Depends(services)) -> dict[str, Any]:
    if not confirm:
        raise InvalidRequest("add confirm=true to delete a stored series")
    return {"deleted": svc.candle_store.delete_series(exchange, pair, timeframe)}
