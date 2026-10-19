from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_reports_components(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "degraded", "error"}
    assert body["components"]["database"]["status"] == "ok"
    # No candles stored and no feed running: market data must not claim to be healthy.
    assert body["components"]["market_data"]["status"] == "error"
    assert body["critical_error"] is None


def test_version_states_no_real_money(client: TestClient) -> None:
    body = client.get("/api/version").json()
    assert body["modes"] == ["paper", "backtest"]
    assert "not implemented" in body["real_money_execution"]


def test_settings_round_trip_and_validation(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body["effective"]["risk"]["risk_per_trade_pct"] == 0.25
    assert body["hard_limits"]["live_trading"] == "not available"
    assert "api_key" not in str(body).lower().replace("anthropic_api_key_present", "")

    ok = client.put("/api/settings", json={"changes": {"risk": {"risk_per_trade_pct": 0.2}}})
    assert ok.status_code == 200, ok.text
    assert ok.json()["effective"]["risk"]["risk_per_trade_pct"] == 0.2
    assert ok.json()["overrides"] == {"risk": {"risk_per_trade_pct": 0.2}}

    bad = client.put("/api/settings", json={"changes": {"risk": {"risk_per_trade_pct": 3.0}}})
    assert bad.status_code == 422
    assert client.get("/api/settings").json()["effective"]["risk"]["risk_per_trade_pct"] == 0.2

    forbidden = client.put("/api/settings", json={"changes": {"risk": {"leverage_enabled": True}}})
    assert forbidden.status_code == 422

    reset = client.delete("/api/settings")
    assert reset.json()["effective"]["risk"]["risk_per_trade_pct"] == 0.25
    assert reset.json()["overrides"] == {}


def test_state_changes_require_client_header(services) -> None:
    app = create_app(services=services, frontend_dist=None)
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"changes": {"risk": {"risk_per_trade_pct": 0.2}}})
        assert r.status_code == 403
        assert r.json()["error"] == "missing_client_header"
        assert c.get("/api/health").status_code == 200


def test_foreign_host_and_origin_rejected(client: TestClient) -> None:
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    r = client.get("/api/health", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.get("/api/health", headers={"origin": "http://localhost:5173"})
    assert ok.status_code == 200
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_unknown_job_is_404(client: TestClient) -> None:
    r = client.get("/api/jobs/nope")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_critical_error_blocks_until_cleared(client: TestClient, services) -> None:
    services.health.raise_critical("test", "simulated failure", detail=1)
    body = client.get("/api/health").json()
    assert body["status"] == "error"
    assert body["critical_error"]["message"] == "simulated failure"
    cleared = client.post("/api/system/critical/clear", json={"note": "checked"})
    assert cleared.json()["critical_error"] is None
