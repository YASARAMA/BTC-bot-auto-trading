import json
import logging
import os
import time
import urllib.error
import urllib.request

import pytest

from bot.config import DOTENV_KEYS
from bot.data.feed import COLUMNS
from bot.ui.app import BotController, prepare_app_dir
from bot.ui.server import UIServer
from tests.conftest import make_ohlcv, trending_series


@pytest.fixture
def ui(tmp_path):
    root = tmp_path / "app"
    prepare_app_dir(root)
    assert (root / "config.yaml").exists() and list((root / "data" / "samples").glob("*.csv"))
    df = make_ohlcv(trending_series(), spread=0.003)
    (root / "data" / "samples" / "synthetic.csv").write_text(df[COLUMNS].to_csv(index=False))
    text = (root / "config.yaml").read_text()
    text = text.replace("rsi_buy_min: 45", "rsi_buy_min: 0").replace("min_seconds_between_trades: 3600", "min_seconds_between_trades: 0")
    (root / "config.yaml").write_text(text)
    controller = BotController(root)
    rootlog = logging.getLogger()
    old_level = rootlog.level
    rootlog.setLevel(logging.INFO)
    rootlog.addHandler(controller.events)
    server = UIServer(controller, port=0)
    server.start()
    try:
        yield root, controller, server
    finally:
        controller.quit(wait=15)
        server.shutdown()
        rootlog.removeHandler(controller.events)
        rootlog.setLevel(old_level)
        for k in DOTENV_KEYS:
            os.environ.pop(k, None)


def call(server, path, body=None, token=None):
    req = urllib.request.Request(server.url.rstrip("/") + path, method="POST" if body is not None else "GET")
    req.add_header("X-Token", server.token if token is None else token)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_for(pred, timeout=30.0, step=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not met in time")


def test_static_page_and_token_auth(ui):
    root, c, server = ui
    with urllib.request.urlopen(server.url, timeout=10) as r:
        html = r.read().decode()
    assert r.status == 200 and server.token in html and "__TOKEN__" not in html
    assert call(server, "/api/status", token="")[0] == 401
    assert call(server, "/api/status", token="wrong")[0] == 401
    code, st = call(server, "/api/status")
    assert code == 200 and st["state"] == "stopped" and st["config"]["symbol"] == "BTC/USDT" and st["version"]
    assert call(server, "/api/nope")[0] == 404
    with urllib.request.urlopen(server.url + "app.js", timeout=10) as r:
        assert r.status == 200 and "X-Token" in r.read().decode()


def test_config_roundtrip_and_validation(ui):
    root, c, server = ui
    code, data = call(server, "/api/config")
    assert code == 200 and data["config"]["risk"]["risk_per_trade_pct"] == 1.0 and "exchange:" in data["yaml"]
    cfg = data["config"]
    cfg["risk"]["risk_per_trade_pct"] = 2.0
    cfg["strategy"]["params"]["ema_fast"] = 10
    code, saved = call(server, "/api/config", {"config": cfg})
    assert code == 200 and saved["config"]["risk"]["risk_per_trade_pct"] == 2.0
    assert call(server, "/api/config")[1]["config"]["strategy"]["params"]["ema_fast"] == 10
    cfg["risk"]["risk_per_trade_pct"] = -1
    code, err = call(server, "/api/config", {"config": cfg})
    assert code == 400 and "risk_per_trade_pct" in err["error"]
    code, _ = call(server, "/api/config", {"yaml": "risk:\n  max_daily_loss_pct: 5\n"})
    assert code == 200 and c.load_cfg().risk.max_daily_loss_pct == 5.0
    assert call(server, "/api/config", {"yaml": "risk:\n  bogus: 1\n"})[0] == 400


def test_secrets_saved_to_env_file_and_never_echoed(ui):
    root, c, server = ui
    code, st = call(server, "/api/secrets", {"EXCHANGE_API_KEY": "key-123", "EXCHANGE_API_SECRET": "sec-456", "LIVE_TRADING": False})
    assert code == 200 and st["api_key_set"] and st["api_secret_set"] and not st["live_trading_env"]
    env_text = (root / ".env").read_text()
    assert "EXCHANGE_API_KEY=key-123" in env_text and "LIVE_TRADING=false" in env_text
    assert "key-123" not in json.dumps(call(server, "/api/secrets")[1])
    assert "key-123" not in json.dumps(call(server, "/api/status")[1])
    call(server, "/api/secrets", {"EXCHANGE_API_KEY": "", "EXCHANGE_API_SECRET": None})  # empty keeps, None clears
    st = call(server, "/api/secrets")[1]
    assert st["api_key_set"] and not st["api_secret_set"]
    assert "EXCHANGE_API_SECRET" not in (root / ".env").read_text()


def test_kill_switch_toggle(ui):
    root, c, server = ui
    assert call(server, "/api/kill", {"active": True})[1]["kill_switch"] is True
    assert (root / "data" / "KILL_SWITCH").exists()
    assert call(server, "/api/status")[1]["kill_switch"] is True
    assert call(server, "/api/kill", {"active": False})[1]["kill_switch"] is False
    assert not (root / "data" / "KILL_SWITCH").exists()


def test_live_start_is_refused_without_both_switches(ui, monkeypatch):
    root, c, server = ui
    monkeypatch.setenv("LIVE_TRADING", "true")
    code, err = call(server, "/api/start", {})
    assert code == 403 and "exchange.live" in err["error"]
    assert call(server, "/api/status")[1]["state"] == "stopped"


def test_demo_replay_runs_and_exposes_data(ui):
    root, c, server = ui
    code, st = call(server, "/api/start", {"replay": "data/samples/synthetic.csv", "replay_delay": 0.03})
    assert code == 200 and st["state"] in ("starting", "running") and st["replay"].endswith("synthetic.csv")
    wait_for(lambda: call(server, "/api/status")[1]["cycles"] >= 20)
    assert call(server, "/api/start", {"replay": "data/samples/synthetic.csv"})[0] == 409
    st = call(server, "/api/status")[1]
    assert st["state"] == "running" and st["mode"] == "paper" and st["signal"] and st["equity"]
    code, ev = call(server, "/api/events?since=0&limit=1000")
    assert code == 200 and any(e.get("event") == "cycle" for e in ev["events"]) and any(e.get("event") == "reconcile" for e in ev["events"])
    last_id = ev["events"][-1]["id"]
    newer = call(server, f"/api/events?since={last_id}")[1]["events"]
    assert all(e["id"] > last_id for e in newer)
    call(server, "/api/stop", {"wait": 10})
    wait_for(lambda: call(server, "/api/status")[1]["state"] == "stopped")
    equity = call(server, "/api/equity")[1]["equity"]
    assert len(equity) >= 20 and all("equity" in p for p in equity)
    trades = call(server, "/api/trades")[1]["trades"]
    assert isinstance(trades, list)
    assert (root / "data" / "bot.sqlite").exists()
    assert call(server, "/api/start", {"replay": "data/samples/missing.csv"})[0] == 404


def test_backtest_job(ui):
    root, c, server = ui
    code, bt = call(server, "/api/backtest", {"csv": "data/samples/synthetic.csv", "cash": 5000})
    assert code == 200 and bt["state"] == "running"
    done = wait_for(lambda: (lambda b: b if b["state"] in ("done", "error") else None)(call(server, "/api/backtest")[1]), timeout=90)
    assert done["state"] == "done", done.get("error")
    assert done["metrics"]["initial_equity"] == 5000 and "Backtest" in done["report"] and len(done["equity"]) > 10
    assert done["source"] == "synthetic.csv" and done["candles"] > 300
    code, bt = call(server, "/api/backtest", {})
    assert code == 200
    wait_for(lambda: call(server, "/api/backtest")[1]["state"] != "running", timeout=60)
    assert call(server, "/api/backtest")[1]["state"] == "error"  # no csv and no dates


def test_samples_and_quit(ui):
    root, c, server = ui
    samples = call(server, "/api/samples")[1]["samples"]
    assert any(s.endswith("synthetic.csv") for s in samples) and any("binance" in s for s in samples)
    assert call(server, "/api/quit", {})[0] == 200
    wait_for(lambda: c.quit_event.is_set())
