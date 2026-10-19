"""Command line: data import, inventory and the research commands on an isolated configuration."""
from __future__ import annotations

from pathlib import Path

import yaml

from app.cli import build_parser, main
from tests.fakes import synthetic_candles


def write_config(tmp_path: Path) -> Path:
    cfg = {
        "exchange": {"websocket_enabled": False},
        "storage": {"database": str(tmp_path / "trader.sqlite"), "market_database": str(tmp_path / "market.sqlite"),
                    "imports_dir": str(tmp_path / "imports"), "reports_dir": str(tmp_path / "reports")},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def test_every_command_is_registered() -> None:
    sub = next(a for a in build_parser()._actions if a.dest == "command")
    assert {"serve", "download", "import-csv", "import-samples", "inventory", "backtest", "validate",
            "selector-validate", "final-test", "paper"} <= set(sub.choices)


def test_import_backtest_and_selector_walk_forward(tmp_path: Path, capsys) -> None:
    cfg = str(write_config(tmp_path))
    csv = tmp_path / "candles.csv"
    synthetic_candles(1400, timeframe="1h", drift=0.0004)[["ts", "open", "high", "low", "close", "volume"]].to_csv(
        csv, index=False)
    # A local file keeps the exchange it really comes from; no network is needed for any of this.
    assert main(["--config", cfg, "import-csv", str(csv), "--exchange", "coinbase", "--pair", "BTC/USDT",
                 "--timeframe", "1h"]) == 0
    assert main(["--config", cfg, "inventory"]) == 0
    assert "coinbase" in capsys.readouterr().out
    scope = ["--exchange", "coinbase", "--pairs", "BTC/USDT", "--timeframe", "1h"]
    assert main(["--config", cfg, "backtest", "--strategy", "momentum_breakout", *scope]) == 0
    out = capsys.readouterr().out
    assert "experiment_id" in out and "cost cost_2x" in out
    assert main(["--config", cfg, "selector-validate", *scope]) == 0
    out = capsys.readouterr().out
    assert "verdict" in out and "alone: momentum_breakout" in out
    assert any((tmp_path / "reports").iterdir())
