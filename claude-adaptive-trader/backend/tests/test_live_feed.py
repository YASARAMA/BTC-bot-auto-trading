from __future__ import annotations

import asyncio
import json

import pytest

from app.config import Settings
from app.data.binance_client import BinancePublicClient
from app.data.historical_store import CandleStore
from app.data.live_feed import LiveFeed, stream_names
from app.data.market_data import MarketDataService
from app.storage.database import Database
from app.storage.schema import MARKET_MIGRATIONS
from tests.fakes import FakeExchange, FakeWebSocket


def test_stream_names() -> None:
    names = stream_names(["BTC/USDT"], ["1h", "15m"])
    assert names == ["btcusdt@ticker", "btcusdt@kline_15m", "btcusdt@kline_1h"]


@pytest.mark.asyncio
async def test_feed_processes_messages_and_reconnects(tmp_path) -> None:
    s = Settings.model_validate({"exchange": {"pairs": ["BTC/USDT"], "extra_pairs": [], "ticker_poll_seconds": 60,
                                              "candle_poll_seconds": 3600}})
    store = CandleStore(Database(tmp_path / "m.sqlite", MARKET_MIGRATIONS))
    ex = FakeExchange()
    market = MarketDataService(lambda: s, store, BinancePublicClient(exchange=ex, sleep=lambda x: None))
    ticker = json.dumps({"stream": "btcusdt@ticker", "data": {"e": "24hrTicker", "s": "BTCUSDT", "c": "50000",
                                                               "b": "49999", "a": "50001", "E": 1}})
    attempts: list[str] = []

    def connect(url, **kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            return FakeWebSocket([ticker], error=ConnectionResetError("dropped"))
        raise OSError("connection refused")

    feed = LiveFeed(lambda: s, market, connect=connect)
    feed.start()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if len(attempts) >= 2:
            break
    await feed.stop()
    assert len(attempts) >= 2, "the feed must reconnect after a dropped connection"
    assert "btcusdt@ticker" in attempts[0] and "btcusdt@kline_15m" in attempts[0]
    assert market.ticker("BTC/USDT")["last"] == 50000.0
    assert market.ws["reconnects"] >= 2 and "refused" in (market.ws["last_error"] or "")
    assert market.ws["connected"] is False
