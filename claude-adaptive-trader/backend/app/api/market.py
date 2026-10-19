"""Market data endpoints: pairs, candles with indicators, tickers, order book, feed status."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import services
from app.core.errors import InvalidRequest
from app.data.chart import CHART_WARMUP, chart_payload

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/pairs")
def pairs(svc=Depends(services)) -> dict[str, Any]:
    s = svc.settings
    local = [
        {"exchange": r["exchange"], "pair": r["pair"], "timeframe": r["timeframe"], "candles": r["candles"],
         "first_ts": r["first_ts"], "last_ts": r["last_ts"]}
        for r in svc.candle_store.series()
    ]
    return {
        "exchange": s.exchange.id,
        "pairs": s.exchange.all_pairs,
        "trading_pairs": s.exchange.pairs,
        "timeframes": s.exchange.timeframes,
        "signal_timeframe": s.exchange.signal_timeframe,
        "confirm_timeframes": s.exchange.confirm_timeframes,
        "local_series": local,
    }


@router.get("/rules")
def rules(pair: str, svc=Depends(services)) -> dict[str, Any]:
    return svc.market.rules(pair).to_dict()


@router.get("/candles")
def candles(pair: str, timeframe: str = "15m", limit: int = Query(500, ge=10, le=3000), end: int | None = None,
            exchange: str = "binance", refresh: bool = True, svc=Depends(services)) -> dict[str, Any]:
    if exchange != "binance" and exchange not in {r["exchange"] for r in svc.candle_store.series()}:
        raise InvalidRequest(f"no local data for exchange {exchange!r}")
    cs = svc.market.candles(pair, timeframe, limit=limit + CHART_WARMUP, end=end, exchange=exchange,
                            refresh=refresh and exchange == "binance")
    return chart_payload(cs, limit)


@router.get("/ticker")
def ticker(pair: str, svc=Depends(services)) -> dict[str, Any]:
    t = svc.market.ticker(pair)
    return {"pair": pair, "ticker": t, "available": t is not None}


@router.get("/tickers")
def tickers(svc=Depends(services)) -> dict[str, Any]:
    return {"tickers": svc.market.tickers()}


@router.get("/orderbook")
def orderbook(pair: str, depth: int = Query(20, ge=5, le=100), svc=Depends(services)) -> dict[str, Any]:
    return svc.market.order_book(pair, depth)


@router.get("/status")
def status(svc=Depends(services)) -> dict[str, Any]:
    return svc.market.status()
