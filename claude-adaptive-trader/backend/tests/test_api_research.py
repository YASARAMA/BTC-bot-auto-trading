"""Research and paper endpoints over HTTP, the way the frontend uses them."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.fakes import synthetic_candles


def seed(services) -> None:
    df = synthetic_candles(4000, "15m", start=1_700_000_000_000, drift=0.0002, vol=0.004, seed=5)
    services.candle_store.upsert("binance", "BTC/USDT", "15m", df, "test")


def wait(client: TestClient, job_id: str, timeout: float = 120.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_backtest_job_and_experiment_endpoints(client: TestClient, services) -> None:
    seed(services)
    r = client.post("/api/backtests", json={"strategy": "momentum_breakout", "pairs": ["BTC/USDT"],
                                            "segment": "development"})
    assert r.status_code == 200, r.text
    job = wait(client, r.json()["id"])
    assert job["status"] == "completed", job
    eid = job["result"]["experiment_id"]
    exp = client.get(f"/api/experiments/{eid}").json()
    assert exp["kind"] == "backtest" and "report.md" in exp["files"] and "base" in exp["scenarios"]
    assert "config_snapshot" not in exp
    assert client.get(f"/api/experiments/{eid}/report.md").text.startswith("# Experiment")
    assert client.get(f"/api/experiments/{eid}/files/base_trades.csv").status_code == 200
    assert client.get(f"/api/experiments/{eid}/files/..%2Fsecret").status_code in {404, 422}
    eq = client.get(f"/api/experiments/{eid}/equity", params={"scenario": "base"}).json()
    assert eq["points"] and "benchmark" in eq["points"][0]
    assert any(e["id"] == eid for e in client.get("/api/experiments").json())
    ds = client.get("/api/datasets").json()
    assert ds and ds[0]["segments"]["test"]["start"] > ds[0]["segments"]["validation"]["end"]


def test_test_segment_is_not_selectable_over_http(client: TestClient) -> None:
    r = client.post("/api/backtests", json={"strategy": "momentum_breakout", "segment": "test"})
    assert r.status_code == 422


def test_paper_replay_over_http(client: TestClient, services) -> None:
    seed(services)
    services.paper.autostart = False
    r = client.post("/api/paper/start", json={"data_mode": "replay", "strategy_mode": "forward_test",
                                              "strategy": "momentum_breakout", "pairs": ["BTC/USDT"],
                                              "timeframe": "15m", "replay_speed": 5000})
    assert r.status_code == 200, r.text
    for _ in range(3000):
        if not services.paper.tick():
            break
    st = client.get("/api/paper/status").json()
    assert st["label"].startswith("HISTORICAL REPLAY") and st["portfolio"]["equity"] > 0
    assert client.get("/api/paper/status-lite").json()["status"] == "running"
    assert isinstance(client.get("/api/portfolio/trades").json(), list)
    assert client.get("/api/portfolio/equity").json()["points"]
    assert client.get("/api/risk/status").json()["checks"][0] == "market_data_freshness"
    ks = client.post("/api/paper/kill-switch", json={"on": True, "reason": "test"}).json()
    assert ks["on"] is True
    assert client.get("/api/paper/status-lite").json()["kill_switch"] is True
    ann = client.get("/api/paper/chart-annotations", params={"pair": "BTC/USDT", "timeframe": "15m"}).json()
    assert "markers" in ann and "levels" in ann
    assert client.post("/api/paper/stop").json()["status"] == "stopped"


def test_strategies_selector_and_regimes_endpoints(client: TestClient, services) -> None:
    seed(services)
    strategies = client.get("/api/strategies").json()
    assert {s["name"] for s in strategies} == {"trend_following", "momentum_breakout", "mean_reversion",
                                               "volatility_breakout"}
    assert all(s["spec"]["entry_rules"] and s["spec"]["required_regimes"] for s in strategies)
    v = client.post("/api/strategies/momentum_breakout/versions", json={"params": {"target_r": 3.0}}).json()
    assert v["status"] == "research" and v["params"]["target_r"] == 3.0
    bad = client.post("/api/strategies/momentum_breakout/versions", json={"params": {"target_r": -1}})
    assert bad.status_code == 422
    refused = client.post(f"/api/strategies/versions/{v['id']}/approve", json={"note": "please"})
    assert refused.status_code == 409                                   # not validated
    ok = client.post(f"/api/strategies/versions/{v['id']}/approve", json={"note": "forward test", "override": True})
    assert ok.json()["status"] == "approved"
    sel = client.get("/api/selector").json()
    assert sel["approved"]["momentum_breakout"] == v["version"]
    reg = client.get("/api/regimes").json()
    assert reg["pairs"]["BTC/USDT"]["available"] and reg["pairs"]["BTC/USDT"]["regime"]
    assert reg["pairs"]["ETH/USDT"]["available"] is False


def test_research_assistant_endpoints_when_disabled(client: TestClient) -> None:
    st = client.get("/api/research/status").json()
    assert st["available"] is False and "cannot place or change orders" in st["limits"]
    note = client.post("/api/research/notes", json={"title": "idea", "body": "check 4h filter"}).json()
    assert note["source"] == "user" and note["kind"] == "journal"
    assert client.get("/api/research/notes").json()[0]["id"] == note["id"]


def test_adaptive_paper_mode_abstains_without_approved_strategies(client: TestClient, services) -> None:
    seed(services)
    services.paper.autostart = False
    r = client.post("/api/paper/start", json={"data_mode": "replay", "strategy_mode": "adaptive",
                                              "pairs": ["BTC/USDT"], "timeframe": "15m"})
    assert r.status_code == 200, r.text
    for _ in range(3000):
        if not services.paper.tick():
            break
    st = client.get("/api/paper/status").json()
    assert st["stats"]["decisions"].get("enter", 0) == 0
    rows = client.get("/api/paper/decisions", params={"action": "no_trade", "limit": 5}).json()
    assert rows and all("no trade" in d["reason"] for d in rows)


def test_dashboard_analytics_logs_and_diagnostics(client: TestClient, services) -> None:
    seed(services)
    empty = client.get("/api/dashboard").json()
    assert empty["bot"]["status"] == "idle" and empty["portfolio"] is None and "simulated" in empty["notice"]
    services.paper.autostart = False
    client.post("/api/paper/start", json={"data_mode": "replay", "strategy_mode": "forward_test",
                                          "strategy": "momentum_breakout", "pairs": ["BTC/USDT"], "timeframe": "15m"})
    for _ in range(3000):
        if not services.paper.tick():
            break
    dash = client.get("/api/dashboard").json()
    assert dash["portfolio"]["equity"] > 0 and dash["returns"]["basis"] == "replay clock"
    assert dash["active_strategies"]["BTC/USDT"]["strategy"] == "momentum_breakout"
    assert dash["performance"]["trades"] == len(client.get("/api/portfolio/trades").json())
    a = client.get("/api/analytics").json()
    assert a["source"] == "paper" and a["equity"] and "monthly_returns" in a and a["drawdown"]
    runs = client.get("/api/analytics/runs").json()
    assert runs and runs[0]["source"] == "paper"
    filtered = client.get("/api/analytics", params={"regime": "range"}).json()
    assert filtered["equity_note"]
    logs = client.get("/api/logs", params={"level": "INFO"}).json()
    assert logs["rows"] and all(r["level"] in {"INFO", "WARNING", "ERROR", "CRITICAL"} for r in logs["rows"])
    diag = client.get("/api/diagnostics").json()
    assert diag["databases"]["state"]["integrity"] == "ok"
    assert diag["safety"]["real_money_execution"] == "not implemented"
