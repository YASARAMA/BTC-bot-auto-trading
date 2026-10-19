"""WebSocket for the browser: live tickers, candle updates and engine events.

The browser subscribes to the candle streams it is showing; tickers and engine events are
always delivered. The Origin is checked here because HTTP middleware does not see
WebSocket handshakes.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import LOOPBACK_HOSTS

router = APIRouter(tags=["stream"])


def _allowed(ws: WebSocket, origins: list[str]) -> bool:
    host = (ws.headers.get("host") or "").split(":")[0]
    if host not in LOOPBACK_HOSTS:
        return False
    origin = ws.headers.get("origin")
    if origin is None:
        return True
    origin = origin.rstrip("/")
    return origin in {o.rstrip("/") for o in origins} or origin.split("//", 1)[-1] == ws.headers.get("host")


@router.websocket("/ws/stream")
async def stream(ws: WebSocket) -> None:
    svc = ws.app.state.services
    if not _allowed(ws, svc.settings.server.cors_origins):
        await ws.close(code=1008)
        return
    await ws.accept()
    subscriptions: set[tuple[str, str]] = set()
    await ws.send_json({"type": "hello", "market": _summary(svc)})

    async def reader() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            op, pair, tf = msg.get("op"), msg.get("pair"), msg.get("timeframe")
            if op == "subscribe" and isinstance(pair, str) and isinstance(tf, str):
                if len(subscriptions) < 20:
                    subscriptions.add((pair, tf))
            elif op == "unsubscribe" and isinstance(pair, str) and isinstance(tf, str):
                subscriptions.discard((pair, tf))
            elif op == "ping":
                await ws.send_json({"type": "pong"})

    async def writer() -> None:
        async for event in svc.bus.subscribe():
            if event.get("type") == "kline" and (event.get("pair"), event.get("timeframe")) not in subscriptions:
                continue
            await ws.send_json(event)

    tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()


def _summary(svc: Any) -> dict[str, Any]:
    st = svc.market.status()
    return {"status": st["status"], "websocket": st["websocket"], "rest": {"reachable": st["rest"].get("reachable"),
                                                                          "last_error": st["rest"].get("last_error")}}
