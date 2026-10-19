"""A small publish/subscribe hub between background threads and WebSocket clients.

Publishers may live on any thread (the paper-trading loop, a download job); subscribers are
asyncio tasks serving browser connections. ``publish`` hops onto the event loop safely.
Slow subscribers lose the oldest events instead of growing memory without bound.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator


class EventBus:
    def __init__(self, queue_size: int = 500) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(event)
        else:
            try:
                loop.call_soon_threadsafe(self._deliver, event)
            except RuntimeError:  # loop shutting down
                pass

    def _deliver(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            with self._lock:
                self._subscribers.discard(q)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subscribers)
