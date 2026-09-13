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


def test_candles_endpoint_from_replay_and_from_exchange(ui):
    root, c, server = ui
    # Stopped: candles come from the (injected) exchange market data, cached for a while.
    from tests.test_feed import FakeMarket

    df = make_ohlcv(trending_series(), spread=0.003)
    fake = FakeMarket(df)
    c.market_factory = lambda cfg: fake
    code, data = call(server, "/api/candles?limit=120")
    assert code == 200 and data["source"] == "exchange" and len(data["candles"]) == 120
    assert data["symbol"] == "BTC/USDT" and set(data["indicators"]) >= {"ema_fast", "ema_slow", "rsi", "atr"}
    assert len(data["indicators"]["ema_fast"]) == 120 and data["position"] is None
    call(server, "/api/candles?limit=120")
    assert fake.calls == 1  # second call served from the cache
    # Running a replay: candles come from the bot's own feed window and carry its trades.
    call(server, "/api/start", {"replay": "data/samples/synthetic.csv", "replay_delay": 0.02})
    wait_for(lambda: call(server, "/api/status")[1]["cycles"] >= 150)
    code, data = call(server, "/api/candles?limit=100")
    assert code == 200 and data["source"] == "replay" and 50 <= len(data["candles"]) <= 100
    # The feed may already hold the candle the engine is about to evaluate: allow one timeframe of skew.
    assert data["candles"][-1][0] <= call(server, "/api/status")[1]["last_cycle"]["candle_ts"] + 3_600_000
    assert isinstance(data["trades"], list) and all("entry_ts" in t for t in data["trades"])
    call(server, "/api/stop", {"wait": 10})
    wait_for(lambda: call(server, "/api/status")[1]["state"] == "stopped")


def test_candles_endpoint_reports_exchange_failure(ui):
    root, c, server = ui

    class Down:
        def fetch_ohlcv(self, *a, **k):
            raise ConnectionError("exchange unreachable")

    c.market_factory = lambda cfg: Down()
    code, data = call(server, "/api/candles?limit=100")
    assert code == 500 and "unreachable" in data["error"]


def test_candles_timeframe_selection_and_config_meta(ui):
    root, c, server = ui
    from tests.test_feed import FakeMarket

    class RecordingMarket(FakeMarket):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            self.timeframes = getattr(self, "timeframes", []) + [timeframe]
            return super().fetch_ohlcv(symbol, timeframe, since, limit)

    fake = RecordingMarket(make_ohlcv(trending_series(), spread=0.003))
    c.market_factory = lambda cfg: fake
    code, data = call(server, "/api/candles?limit=80&timeframe=15m")
    assert code == 200 and data["timeframe"] == "15m" and data["bot_timeframe"] == "1h" and fake.timeframes == ["15m"]
    code, data = call(server, "/api/candles?limit=80")
    assert code == 200 and data["timeframe"] == "1h" and fake.timeframes == ["15m", "1h"]
    code, err = call(server, "/api/candles?timeframe=7x")
    assert code == 400 and "timeframe" in err["error"]
    meta = call(server, "/api/config")[1]
    assert "ema_rsi" in meta["strategies"] and "1h" in meta["timeframes"] and "binance" in meta["exchanges"]
    assert call(server, "/api/status")[1]["paths"]["temporary"] in (True, False)


def test_update_endpoints(ui):
    root, c, server = ui
    from tests.test_updater import release

    code, st = call(server, "/api/update")
    assert code == 200 and st["current"]["version"] and st["latest"] is None and st["can_install"] is False
    c.updater.fetch_json = lambda url, token: release(tag="v99.0.0-build.1")
    code, st = call(server, "/api/update", {"action": "check"})
    assert code == 200 and st["available"] is True and st["latest"]["tag"] == "v99.0.0-build.1"
    assert call(server, "/api/status")[1]["update"]["available"] is True
    assert any(a.get("event") == "update_available" for a in c.recent_alerts)
    code, err = call(server, "/api/update", {"action": "install"})
    assert code == 400 and "packaged app" in err["error"]  # tests run from source
    assert call(server, "/api/update", {"action": "bogus"})[0] == 400
    cfg = call(server, "/api/config")[1]["config"]
    assert cfg["update"]["auto_install"] is True and cfg["update"]["repo"].startswith("YASARAMA/")


def test_mode_switching_rewrites_risk_and_strategy(ui):
    root, c, server = ui
    st = call(server, "/api/status")[1]
    assert st["trading_mode"] == "balanced" and len(st["trading_modes"]) == 3
    code, out = call(server, "/api/mode", {"mode": "aggressive"})
    assert code == 200 and out["config"]["risk"]["risk_per_trade_pct"] == 2.0
    assert out["config"]["risk"]["max_position_pct"] == 50.0 and out["config"]["mode"] == "aggressive"
    assert out["config"]["strategy"]["params"]["atr_stop_mult"] == 1.5
    assert c.load_cfg().mode == "aggressive"  # persisted to config.yaml
    st = call(server, "/api/status")[1]
    assert st["trading_mode"] == "aggressive" and st["config"]["mode_matches"] is True
    assert any(a.get("event") == "mode_changed" for a in c.recent_alerts)
    assert call(server, "/api/mode", {"mode": "nonsense"})[0] == 400
    # Hand-editing one number makes the config stop matching the mode.
    cfg = call(server, "/api/config")[1]["config"]
    cfg["risk"]["risk_per_trade_pct"] = 1.23
    call(server, "/api/config", {"config": cfg})
    assert call(server, "/api/status")[1]["config"]["mode_matches"] is False


def test_manual_order_api(ui):
    root, c, server = ui
    # Refused while the bot is stopped: manual orders need its exchange connection.
    code, err = call(server, "/api/order", {"side": "buy", "quote_amount": 100})
    assert code == 400 and "start the bot" in err["error"].lower()

    call(server, "/api/start", {"replay": "data/samples/synthetic.csv", "replay_delay": 0.4})
    wait_for(lambda: call(server, "/api/status")[1]["state"] == "running" and call(server, "/api/status")[1]["price"])
    assert call(server, "/api/order", {"side": "sideways"})[0] == 400
    assert call(server, "/api/order", {"side": "sell"})[0] == 400  # nothing to sell yet

    code, out = call(server, "/api/order", {"side": "buy", "quote_amount": 500})
    assert code == 200, out
    assert out["order"]["side"] == "buy" and out["order"]["filled"] > 0
    assert out["position"]["qty"] == pytest.approx(out["order"]["filled"])
    assert any(a.get("event") == "manual_order" for a in c.recent_alerts)

    code, pos = call(server, "/api/levels", {"stop_loss": out["price"] * 0.9, "take_profit": out["price"] * 1.2})
    assert code == 200 and pos["stop_loss"] == pytest.approx(out["price"] * 0.9)
    assert call(server, "/api/levels", {"stop_loss": out["price"] * 2})[0] == 400

    code, sold = call(server, "/api/order", {"side": "sell"})
    assert code == 200 and sold["position"] is None
    assert call(server, "/api/trades")[1]["trades"], "the manual round trip is in the history"

    # The kill switch blocks manual orders as well.
    call(server, "/api/kill", {"active": True})
    code, err = call(server, "/api/order", {"side": "buy", "quote_amount": 100})
    assert code == 403 and "kill switch" in err["error"]
    call(server, "/api/kill", {"active": False})
    call(server, "/api/stop", {"wait": 10})


def test_log_and_debug_channels(ui):
    root, c, server = ui
    call(server, "/api/start", {"replay": "data/samples/synthetic.csv", "replay_delay": 0.02})
    wait_for(lambda: call(server, "/api/status")[1]["cycles"] >= 40)
    call(server, "/api/stop", {"wait": 10})
    everything = call(server, "/api/events?since=0&limit=2000")[1]["events"]
    log_only = call(server, "/api/events?since=0&limit=2000&channel=log")[1]["events"]
    debug_only = call(server, "/api/events?since=0&limit=2000&channel=debug")[1]["events"]
    assert everything and log_only and debug_only
    assert len(log_only) + len(debug_only) == len(everything)
    assert all(e["channel"] == "log" for e in log_only)
    assert all(e.get("event") == "cycle" or e["level"] == "DEBUG" for e in debug_only)
    assert any(e.get("event") == "reconcile" for e in log_only)
    assert not any(e.get("event") == "cycle" for e in log_only), "routine cycles belong in the debug channel"


def test_ai_status_is_reported(ui, monkeypatch):
    root, c, server = ui
    st = call(server, "/api/status")[1]["ai"]
    assert st["enabled"] is False and st["key_set"] is False
    call(server, "/api/secrets", {"ANTHROPIC_API_KEY": "sk-ant-test"})
    cfg = call(server, "/api/config")[1]["config"]
    cfg["strategy"] = {"name": "ai", "params": {"model": "claude-sonnet-5", "fallback_to_technical": True}}
    assert call(server, "/api/config", {"config": cfg})[0] == 200
    st = call(server, "/api/status")[1]["ai"]
    assert st["enabled"] is True and st["key_set"] is True and st["model"] == "claude-sonnet-5"
    assert "sk-ant-test" not in json.dumps(call(server, "/api/status")[1])


def test_license_api_and_live_gate(ui, monkeypatch):
    root, c, server = ui
    import csv as _csv
    from pathlib import Path as _Path

    code, lic = call(server, "/api/license")
    assert code == 200 and lic["licensed"] is False and lic["keys_in_build"] == 100
    assert call(server, "/api/license", {"action": "activate", "key": "nope"})[0] == 400
    assert call(server, "/api/license", {"action": "bogus"})[0] == 400

    private = _Path("licenses-private.csv")
    if not private.exists():
        pytest.skip("the private key list is not on this machine")
    key = next(iter(_csv.DictReader(private.open(newline="", encoding="utf-8"))))["key"]

    # Live start is refused while unlicensed, even with both safety switches set.
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("EXCHANGE_API_KEY", "k")
    monkeypatch.setenv("EXCHANGE_API_SECRET", "s")
    cfg = call(server, "/api/config")[1]["config"]
    cfg["exchange"]["live"] = True
    call(server, "/api/config", {"config": cfg})
    code, err = call(server, "/api/start", {"confirm_live": True})
    assert code in (400, 403) and "license" in err["error"].lower()

    code, out = call(server, "/api/license", {"action": "activate", "key": key.lower()})
    assert code == 200 and out["licensed"] is True and out["accepted"] is True
    assert key not in json.dumps(out) and out["masked"].endswith(key.split("-")[-1])
    assert call(server, "/api/status")[1]["license"]["licensed"] is True
    assert (root / "data" / "license.json").exists()

    assert call(server, "/api/license", {"action": "remove"})[1]["licensed"] is False


def test_changelog_endpoint(ui):
    root, c, server = ui
    code, data = call(server, "/api/changelog")
    assert code == 200 and data["entries"], "the build ships CHANGELOG.md"
    newest = data["entries"][0]
    assert newest["version"] == data["current"], "the newest entry matches this build"
    assert "- " in newest["body"]
    one = call(server, f"/api/changelog?version={newest['version']}")[1]
    assert len(one["entries"]) == 1 and one["entries"][0]["version"] == newest["version"]


def test_backtest_date_error_names_the_files_range(ui):
    root, c, server = ui
    code, started = call(server, "/api/backtest", {"csv": "data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv",
                                                   "from": "2025-07-10", "to": "2025-07-12"})
    assert code == 200
    done = wait_for(lambda: (lambda b: b if b["state"] in ("done", "error") else None)(call(server, "/api/backtest")[1]), timeout=60)
    assert done["state"] == "error"
    err = done["error"]
    assert "2020-11-17" in err and "2021-05-16" in err and "2025-07-10" in err
    assert "clear both date fields" in err


def test_sample_details_report_the_covered_period(ui):
    root, c, server = ui
    code, data = call(server, "/api/samples")
    assert code == 200 and data["details"]
    by_name = {d["name"]: d for d in data["details"]}
    binance = by_name["binance_BTCUSDT_1h_2020-11_2021-05.csv"]
    assert binance["from"] == "2020-11-17" and binance["to"] == "2021-05-16" and binance["candles"] == 4309
    assert set(data["samples"]) == {d["path"] for d in data["details"]}


def test_why_no_trades_explains_the_hold(ui):
    root, c, server = ui
    code, why = call(server, "/api/why")
    assert code == 200 and "The bot is stopped. Press Start." in why["blockers"]

    call(server, "/api/start", {"replay": "data/samples/synthetic.csv", "replay_delay": 0.02})
    wait_for(lambda: call(server, "/api/status")[1]["cycles"] >= 120)
    why = call(server, "/api/why")[1]
    assert why["candles_evaluated"] > 0
    assert sum(why["decisions"].values()) == why["candles_evaluated"]
    assert "no entry signal" in why["decisions"] or "warming up" in why["decisions"]
    assert why["recent"] and why["recent"][0]["action"] in ("BUY", "SELL", "HOLD")
    assert why["timeframe"] == "1h" and why["mode"] == "balanced"
    assert why["hints"] and not why["blockers"]
    if why["ema_gap_pct"] is not None:
        assert "crosses above" in why["waiting"]

    call(server, "/api/kill", {"active": True})
    assert "kill switch" in " ".join(call(server, "/api/why")[1]["blockers"]).lower()
    call(server, "/api/kill", {"active": False})
    call(server, "/api/stop", {"wait": 10})


def test_research_walk_forward_via_api(ui):
    root, c, server = ui
    # Real candles: a walk-forward test needs enough history for two blocks that both trade.
    csv = "data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv"
    code, started = call(server, "/api/research", {"csv": csv, "mode": "walk-forward", "folds": 2,
                                                   "sample": 4, "min_trades": 2})
    assert code == 200 and started["state"] == "running"
    assert call(server, "/api/research", {"csv": csv})[0] == 409
    done = wait_for(lambda: (lambda r: r if r["state"] in ("done", "error") else None)(call(server, "/api/research")[1]),
                    timeout=240)
    assert done["state"] == "done", done.get("error")
    assert done["mode"] == "walk-forward" and len(done["folds"]) == 2
    assert "out_of_sample" in done and done["verdict"]
    for fold in done["folds"]:
        assert fold["chosen"] and fold["test_metrics"]

    chosen = done["folds"][-1]["chosen"]
    code, applied = call(server, "/api/research/apply", {"params": chosen})
    assert code == 200
    saved = call(server, "/api/config")[1]["config"]["strategy"]["params"]
    for k, v in chosen.items():
        assert saved[k] == v, f"{k} was written to the configuration"
    assert call(server, "/api/research/apply", {"params": {}})[0] == 400


def test_research_grid_mode_marks_results_as_in_sample(ui):
    root, c, server = ui
    call(server, "/api/research", {"csv": "data/samples/synthetic.csv", "mode": "grid", "sample": 3, "min_trades": 1})
    done = wait_for(lambda: (lambda r: r if r["state"] in ("done", "error") else None)(call(server, "/api/research")[1]),
                    timeout=180)
    assert done["state"] == "done" and done["mode"] == "grid"
    assert done["top"] and "in-sample" in done["note"]
    assert all("params" in c and "metrics" in c for c in done["top"])


def test_analytics_and_csv_export(ui):
    root, c, server = ui
    # A hand-placed round trip gives the analytics something deterministic to summarise.
    call(server, "/api/start", {"replay": "data/samples/synthetic.csv", "replay_delay": 0.4})
    wait_for(lambda: call(server, "/api/status")[1]["state"] == "running" and call(server, "/api/status")[1]["price"])
    assert call(server, "/api/order", {"side": "buy", "quote_amount": 500})[0] == 200
    assert call(server, "/api/order", {"side": "sell"})[0] == 200
    call(server, "/api/stop", {"wait": 10})
    wait_for(lambda: len(call(server, "/api/trades")[1]["trades"]) > 0, timeout=30)

    code, a = call(server, "/api/analytics")
    assert code == 200, a
    assert a["trades"] > 0
    assert a["monthly"] and a["by_exit"] and "expectancy" in a
    assert "exit" in a["by_exit"], "the manual sell is recorded as a plain exit"
    assert a["win_rate_pct"] >= 0 and a["durations"]["median_hours"] >= 0

    import urllib.request

    for what, header in (("trades", "entry_time,exit_time"), ("equity", "time,equity")):
        url = f"{server.url}api/export?what={what}&token={server.token}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode()
            assert resp.headers["Content-Type"].startswith("text/csv")
            assert f'filename="{what}.csv"' in resp.headers["Content-Disposition"]
        assert body.splitlines()[0].startswith(header)
        assert len(body.splitlines()) > 1
    # the token is still required
    try:
        urllib.request.urlopen(f"{server.url}api/export?what=trades", timeout=10)
        raise AssertionError("an export without a token must be refused")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401


def test_history_download_uses_the_exchange_and_caches(ui):
    root, c, server = ui
    from tests.test_feed import FakeMarket

    frame = make_ohlcv(trending_series(), spread=0.002)
    fake = FakeMarket(frame)
    c.market_factory = lambda cfg: fake
    from bot.common import iso as _iso

    code, started = call(server, "/api/download", {
        "symbol": "BTC/USDT", "timeframe": "1h",
        "from": _iso(int(frame["ts"].iloc[0]))[:10], "to": _iso(int(frame["ts"].iloc[-1]))[:10]})
    assert code == 200 and started["state"] == "running"
    done = wait_for(lambda: (lambda d: d if d["state"] in ("done", "error") else None)(call(server, "/api/download")[1]),
                    timeout=60)
    assert done["state"] == "done", done.get("error")
    assert done["candles"] > 0 and done["file"].endswith(".csv")
    assert (root / done["file"]).exists()
    assert any(s["path"] == done["file"].replace("\\", "/") for s in call(server, "/api/samples")[1]["details"])


def test_telegram_commands_are_answered_and_restricted(ui):
    root, c, server = ui
    from bot.notify.telegram_control import TelegramControl

    sent: list[dict] = []

    def fake_request(method, params):
        if method == "getUpdates":
            return {"result": [
                {"update_id": 1, "message": {"chat": {"id": 77}, "text": "/status"}},
                {"update_id": 2, "message": {"chat": {"id": 77}, "text": "/pnl"}},
                {"update_id": 3, "message": {"chat": {"id": 77}, "text": "/kill"}},
                {"update_id": 4, "message": {"chat": {"id": 999}, "text": "/kill"}},
            ]}
        sent.append(params)
        return {"ok": True}

    tc = TelegramControl("token", "77", c._telegram_handlers(), request=fake_request)
    assert tc.poll_once() == 3
    assert tc.rejected == 1, "a message from another chat is ignored"
    texts = [p["text"] for p in sent]
    assert any("stopped" in t or "running" in t for t in texts)
    assert any("No closed trades" in t or "trades" in t for t in texts)
    assert c.kill_switch_path().exists(), "/kill turned the kill switch on"
    assert "Kill switch ON" in texts[-1]
    c.kill_switch(False)
    assert "Unknown command" in tc.handle("/nonsense")
    assert "/status" in tc.handle("/help")


def test_watchdog_alerts_when_cycles_stop(ui):
    root, c, server = ui
    from bot.notify.telegram_control import Watchdog

    alerts: list[str] = []
    running = {"on": True}
    last = {"ms": 1_000_000}
    w = Watchdog(is_running=lambda: running["on"], last_cycle_ms=lambda: last["ms"],
                 notify=alerts.append, stall_seconds=600)
    assert w.check(now=1_000_000 + 60_000) is None, "a fresh cycle is fine"
    assert w.check(now=1_000_000 + 900_000), "no cycle for 15 minutes raises the alarm"
    assert "not being watched" in alerts[0]
    assert w.check(now=1_000_000 + 1_000_000) is None, "it does not repeat the same alert"
    assert len(alerts) == 1
    last["ms"] = 3_000_000
    w.check(now=3_000_000 + 60_000)
    assert "resumed" in alerts[-1]
    running["on"] = False
    assert w.check(now=9_000_000) is None, "a stopped bot is not an emergency"
