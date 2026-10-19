"""Live updates from Binance's public WebSocket streams, with a REST safety net.

One combined-stream connection carries kline updates for every configured pair and
timeframe plus the 24h ticker (last price, change, volume, best bid/ask). When the
connection drops, it reconnects with exponential backoff, and every reconnect is followed
by a REST top-up so that candles which closed while it was down are not lost.

Independently, a polling loop refreshes tickers over REST whenever the stream is quiet and
tops up the recent candles of the timeframes the strategies use. If Binance is unreachable
from this machine, both loops say so in the status and back off; nothing is made up.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from app.config import Settings
from app.core.clock import now_ms
from app.core.timeframes import ordered
from app.data.market_data import MarketDataService, ws_symbol
from app.monitoring.logging import get_logger, log_event

log = get_logger("live_feed")


def stream_names(pairs: list[str], timeframes: list[str]) -> list[str]:
    out = []
    for pair in pairs:
        sym = ws_symbol(pair)
        out.append(f"{sym}@ticker")
        out.extend(f"{sym}@kline_{tf}" for tf in ordered(timeframes))
    return out


class LiveFeed:
    def __init__(self, settings: Callable[[], Settings], market: MarketDataService, *,
                 connect: Callable[..., Any] | None = None) -> None:
        self._settings = settings
        self.market = market
        self._connect = connect
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._tasks = [loop.create_task(self._ws_loop(), name="binance-ws"),
                       loop.create_task(self._poll_loop(), name="binance-poll")]

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        self.market.set_ws_state(connected=False)

    async def _sleep(self, seconds: float) -> bool:
        """Sleep unless asked to stop; returns True when stopping."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    # ----- WebSocket ---------------------------------------------------------------------------
    async def _ws_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            s = self._settings()
            if not s.exchange.websocket_enabled:
                self.market.set_ws_state(enabled=False, connected=False)
                if await self._sleep(5):
                    return
                continue
            pairs = s.exchange.all_pairs
            streams = stream_names(pairs, s.exchange.timeframes)
            symbols = {ws_symbol(p): p for p in pairs}
            url = f"{s.exchange.websocket_url}?streams={'/'.join(streams)}"
            self.market.set_ws_state(enabled=True, streams=len(streams))
            try:
                connect = self._connect
                if connect is None:
                    import websockets

                    connect = websockets.connect
                async with connect(url, open_timeout=10, ping_interval=20, ping_timeout=20, close_timeout=5,
                                   max_size=2 ** 20) as ws:
                    self.market.set_ws_state(connected=True, since_ms=now_ms(), last_error=None)
                    log_event(log, 20, "Binance WebSocket connected", streams=len(streams))
                    backoff = 1.0
                    asyncio.get_running_loop().create_task(self._backfill(pairs, s))
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        try:
                            message = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        self.market.handle_ws_message(message, symbols)
                        current = self._settings()
                        if (current.exchange.all_pairs != pairs or current.exchange.timeframes != s.exchange.timeframes
                                or not current.exchange.websocket_enabled):
                            break  # configuration changed: reconnect with the new stream list
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure means the same thing: reconnect later
                was_connected = self.market.ws.get("connected")
                self.market.set_ws_state(connected=False, last_error=f"{type(exc).__name__}: {str(exc)[:200]}",
                                         reconnects=self.market.ws.get("reconnects", 0) + 1)
                if was_connected or self.market.ws["reconnects"] == 1:
                    log_event(log, 30, "Binance WebSocket unavailable; retrying with backoff",
                              error=self.market.ws["last_error"])
            else:
                self.market.set_ws_state(connected=False)
            if await self._sleep(backoff):
                return
            backoff = min(backoff * 2, 60.0)

    async def _backfill(self, pairs: list[str], s: Settings) -> None:
        tfs = ordered([s.exchange.signal_timeframe, *s.exchange.confirm_timeframes])
        for pair in pairs:
            for tf in tfs:
                await asyncio.to_thread(self.market.refresh_recent, pair, tf, 300)

    # ----- REST polling ----------------------------------------------------------------------------
    async def _poll_loop(self) -> None:
        last_candles = 0.0
        while not self._stop.is_set():
            s = self._settings()
            ws = self.market.ws
            quiet = (not ws.get("connected") or not ws.get("last_message_ms")
                     or now_ms() - ws["last_message_ms"] > 30_000)
            if quiet:
                await asyncio.to_thread(self.market.refresh_tickers)
            loop_time = asyncio.get_running_loop().time()
            if loop_time - last_candles >= s.exchange.candle_poll_seconds:
                last_candles = loop_time
                tfs = ordered([s.exchange.signal_timeframe, *s.exchange.confirm_timeframes])
                for pair in s.exchange.all_pairs:
                    for tf in tfs:
                        if self._stop.is_set():
                            return
                        await asyncio.to_thread(self.market.refresh_recent, pair, tf, 300)
            if await self._sleep(s.exchange.ticker_poll_seconds):
                return
