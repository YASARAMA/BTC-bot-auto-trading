from __future__ import annotations

from fastapi.testclient import TestClient

from tests.fakes import synthetic_candles


def seed(services, n: int = 900) -> None:
    df = synthetic_candles(n, "15m", start=1_600_000_000_000)
    services.candle_store.upsert("binance", "BTC/USDT", "15m", df, "binance-rest")


def test_candles_with_indicators(client: TestClient, services) -> None:
    seed(services)
    r = client.get("/api/market/candles", params={"pair": "BTC/USDT", "timeframe": "15m", "limit": 200,
                                                  "refresh": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["candles"]) == 200 and body["data_label"] == "Binance public market data"
    assert body["freshness"]["stale"] is True          # old synthetic data, honestly reported as stale
    ema200 = body["indicators"]["ema200"]
    assert len(ema200) == 200 and all(p["value"] is not None for p in ema200)   # warm-up happened off-screen
    assert body["candles"][0]["time"] == body["candles"][0]["ts"] // 1000


def test_warmup_values_are_null(client: TestClient, services) -> None:
    seed(services, n=150)
    body = client.get("/api/market/candles", params={"pair": "BTC/USDT", "timeframe": "15m", "limit": 150,
                                                     "refresh": False}).json()
    ema200 = [p["value"] for p in body["indicators"]["ema200"]]
    assert all(v is None for v in ema200)              # 150 candles cannot produce an EMA 200
    rsi = [p["value"] for p in body["indicators"]["rsi14"]]
    assert rsi[:14] == [None] * 14 and rsi[14] is not None


def test_missing_series_is_503(client: TestClient) -> None:
    r = client.get("/api/market/candles", params={"pair": "ETH/USDT", "timeframe": "4h", "refresh": False})
    assert r.status_code == 503 and r.json()["error"] == "data_unavailable"


def test_import_upload_and_inventory(client: TestClient) -> None:
    csv = synthetic_candles(48, "1h").to_csv(index=False).encode()
    r = client.post("/api/data/import", files={"file": ("btc.csv", csv, "text/csv")},
                    data={"exchange": "binance", "pair": "btc/usdt", "timeframe": "1h"})
    assert r.status_code == 200, r.text
    assert r.json()["written"] == 48
    inv = client.get("/api/data/inventory").json()
    assert inv["series"][0]["sources"] == {"local-file:btc.csv": 48}
    assert {"pair": "BTC/USDT", "timeframe": "15m"} in inv["missing_for_strategies"]


def test_sync_job_runs_against_fake_exchange(client: TestClient, services, fake_exchange) -> None:
    import time

    fake_exchange.series[("BTC/USDT", "15m")] = synthetic_candles(300, "15m", start=1_600_000_000_000)
    job = client.post("/api/data/sync", json={"pairs": ["BTC/USDT"], "timeframes": ["15m"], "days": 3650}).json()
    for _ in range(100):
        state = client.get(f"/api/jobs/{job['id']}").json()
        if state["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert state["status"] == "completed", state
    assert state["result"][0]["written"] == 300


def test_market_status_and_pairs(client: TestClient) -> None:
    st = client.get("/api/market/status").json()
    assert st["exchange"] == "binance" and "websocket" in st and "rest" in st
    pairs = client.get("/api/market/pairs").json()
    assert pairs["trading_pairs"] == ["BTC/USDT", "ETH/USDT"] and "SOL/USDT" in pairs["pairs"]


def test_stream_websocket_hello_and_filtering(client: TestClient, services) -> None:
    with client.websocket_connect("/ws/stream") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        ws.send_json({"op": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_stream_rejects_foreign_origin(client: TestClient) -> None:
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/stream", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
