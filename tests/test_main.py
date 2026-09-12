import json
import subprocess
import sys

import pytest

from bot.data.feed import COLUMNS
from bot.main import main
from tests.conftest import make_ohlcv, trending_series


@pytest.fixture
def workdir(tmp_path):
    df = make_ohlcv(trending_series(), spread=0.003)
    csv = tmp_path / "candles.csv"
    df[COLUMNS].to_csv(csv, index=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "exchange: {poll_interval_seconds: 0.01}\n"
        "strategy:\n  name: ema_rsi\n  params: {rsi_buy_min: 0, rsi_buy_max: 100}\n"
        f"risk: {{kill_switch_file: '{tmp_path / 'KILL'}', min_seconds_between_trades: 0}}\n"
        f"state: {{db_path: '{tmp_path / 'bot.sqlite'}'}}\n"
        "logging: {format: json}\n"
    )
    return tmp_path, csv, cfg


def test_replay_loop_runs_cycles_and_persists(workdir, capsys, monkeypatch):
    tmp_path, csv, cfg = workdir
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    rc = main(["--config", str(cfg), "--replay", str(csv), "--max-cycles", "200"])
    assert rc == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    events = [l.get("event") for l in lines]
    assert events[0] == "startup" and lines[0]["mode"] == "paper"
    assert "reconcile" in events
    cycles = [l for l in lines if l.get("event") == "cycle"]
    assert len(cycles) == 200
    assert all("signal" in c for c in cycles)  # every replay cycle saw a new closed candle
    assert not any(l.get("event") == "cycle_error" for l in lines)
    assert any(l.get("event") == "position_opened" for l in lines)

    # Second run resumes from the store: the first candle is already processed.
    rc = main(["--config", str(cfg), "--replay", str(csv), "--replay-start", "90", "--max-cycles", "3"])
    out = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    assert rc == 0
    resumed = [l for l in out if l.get("event") == "cycle"]
    assert any("skipped" in c for c in resumed)


def test_live_refused_without_both_switches(workdir, monkeypatch, capsys):
    tmp_path, csv, cfg = workdir
    monkeypatch.setenv("LIVE_TRADING", "true")
    assert main(["--config", str(cfg), "--replay", str(csv), "--once"]) == 2
    assert "REFUSED" in capsys.readouterr().err
    monkeypatch.delenv("LIVE_TRADING")
    live_cfg = tmp_path / "live.yaml"
    live_cfg.write_text(cfg.read_text() + "\n")
    live_cfg.write_text(cfg.read_text().replace("exchange: {", "exchange: {live: true, "))
    assert main(["--config", str(live_cfg), "--replay", str(csv), "--once"]) == 2


def test_module_entrypoints_exist():
    for mod in ("bot.main", "bot.backtest"):
        out = subprocess.run([sys.executable, "-m", mod, "--help"], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
