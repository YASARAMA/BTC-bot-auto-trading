import math

import pandas as pd
import pytest

from bot.backtest import run_backtest
from bot.backtest.__main__ import format_report, main as backtest_main
from bot.backtest.metrics import compute_metrics, max_drawdown_pct, sharpe_ratio
from bot.models import Trade
from tests.conftest import make_ohlcv, trending_series


def wavy(n=900, base=100.0):
    return [base * (1 + 0.15 * math.sin(i / 40.0)) * (1 + 0.0002 * i) for i in range(n)]


def test_backtest_runs_and_reports(cfg):
    df = make_ohlcv(wavy(), spread=0.002)
    res = run_backtest(df, cfg, strategy_name="ema_rsi", params={"rsi_buy_min": 0, "rsi_buy_max": 100})
    m = res.metrics
    for key in ("total_return_pct", "max_drawdown_pct", "sharpe", "win_rate_pct", "profit_factor", "trades",
                "exposure_pct", "buy_hold_return_pct", "final_equity"):
        assert key in m
    assert m["trades"] > 0
    assert m["trades"] == len(res.trades)
    assert -100.0 <= m["max_drawdown_pct"] <= 0.0
    assert 0.0 <= m["exposure_pct"] <= 100.0
    assert len(res.equity) >= len(df) - res.strategy["warmup"]
    assert all(t.exit_reason in {"stop_loss", "take_profit", "exit"} for t in res.trades)
    assert res.metrics["final_equity"] == pytest.approx(res.equity["equity"].iloc[-1], rel=1e-6)
    report = format_report(res, show_trades=3)
    assert "Total return" in report and "does not predict" in report


def test_backtest_is_deterministic(cfg):
    df = make_ohlcv(wavy(), spread=0.002)
    a = run_backtest(df, cfg, params={"rsi_buy_min": 0, "rsi_buy_max": 100})
    b = run_backtest(df, cfg, params={"rsi_buy_min": 0, "rsi_buy_max": 100})
    assert a.metrics == b.metrics


def test_backtest_uses_risk_limits(cfg):
    df = make_ohlcv(wavy(), spread=0.002)
    res = run_backtest(df, cfg, params={"rsi_buy_min": 0, "rsi_buy_max": 100})
    eq = res.equity.set_index("ts")
    for t in res.trades:
        equity_at_entry = eq["equity"].loc[: t.entry_ts - 1].iloc[-1] if (eq.index < t.entry_ts).any() else cfg.paper.initial_cash
        assert t.qty * t.entry_price <= equity_at_entry * cfg.risk.max_position_pct / 100.0 * 1.01


def test_metrics_math():
    eq = pd.DataFrame({"ts": [0, 1, 2, 3], "equity": [100.0, 110.0, 99.0, 120.0], "position_qty": [0, 1, 1, 0]})
    assert max_drawdown_pct(eq["equity"]) == pytest.approx(-10.0)
    assert sharpe_ratio(pd.Series([1.0, 1.0, 1.0]), 3_600_000) == 0.0
    trades = [Trade("s", "x", 1, 0, 1, 1, 2, 0.1, 10.0, 1, "exit", "a", "b"),
              Trade("s", "x", 1, 1, 1, 2, 0.5, 0.1, -5.0, -1, "stop_loss", "a", "b")]
    m = compute_metrics(eq, trades, tf_ms=3_600_000, initial_cash=100.0, first_price=10.0, last_price=12.0)
    assert m["total_return_pct"] == pytest.approx(20.0)
    assert m["buy_hold_return_pct"] == pytest.approx(20.0)
    assert m["win_rate_pct"] == 50.0 and m["profit_factor"] == 2.0 and m["trades"] == 2
    assert m["exposure_pct"] == 50.0 and m["exit_reasons"] == {"exit": 1, "stop_loss": 1}


def test_cli_with_csv(tmp_path, capsys):
    df = make_ohlcv(wavy(), spread=0.002)
    csv = tmp_path / "candles.csv"
    df.to_csv(csv, index=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("risk:\n  kill_switch_file: ''\nstrategy:\n  name: ema_rsi\n  params: {rsi_buy_min: 0, rsi_buy_max: 100}\n")
    rc = backtest_main(["--csv", str(csv), "--config", str(cfg_path), "--strategy", "ema_rsi",
                        "--param", "atr_stop_mult=2.5", "--show-trades", "2"])
    out = capsys.readouterr().out
    assert rc == 0 and "Backtest" in out and "Trades" in out


def test_a_filter_that_needs_more_history_widens_the_window_instead_of_going_quiet(cfg):
    """A daily trend filter needs weeks of hourly candles. If the rolling window stayed at
    candle_history the filter would answer "not ready" on every candle and the run would
    make no trades at all - which looks like a strategy that never triggers."""
    from bot.data.history import load_csv

    df = load_csv("data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv")
    params = {"entry_period": 20, "exit_period": 10}
    daily = cfg.model_copy(update={
        "exchange": cfg.exchange.model_copy(update={"candle_history": 300}),
        "filters": cfg.filters.model_copy(update={"htf_factor": 24, "htf_period": 20, "htf_mode": "rising"}),
    })
    filtered = run_backtest(df, daily, strategy_name="breakout", params=params)
    assert filtered.metrics["trades"] > 0, "the filter must be able to answer"

    plain = run_backtest(df, cfg, strategy_name="breakout", params=params)
    assert filtered.metrics["trades"] < plain.metrics["trades"], "and it must remove some entries"


def test_a_range_too_short_for_the_filters_runs_without_them_and_says_so(cfg):
    """Refusing the run would be worse: the filters are a default the user did not choose
    for this range. But a backtest whose settings quietly differ from the live bot's is
    not worth having, so the result carries the note."""
    daily = cfg.model_copy(update={
        "filters": cfg.filters.model_copy(update={"htf_factor": 24, "htf_period": 50}),
    })
    short = make_ohlcv(trending_series(n_down=60, n_up=60, n_down2=60))
    result = run_backtest(short, daily, strategy_name="breakout", params={"entry_period": 20, "exit_period": 10})
    assert result.notes and "entry filters were off" in result.notes[0]
    assert str(len(short)) in result.notes[0]

    from bot.backtest.__main__ import format_report

    assert "NOTE" in format_report(result, show_trades=0)


def test_a_range_too_short_for_the_strategy_still_fails(cfg):
    with pytest.raises(ValueError, match="need at least"):
        run_backtest(make_ohlcv([100.0] * 30), cfg, strategy_name="breakout",
                     params={"entry_period": 20, "exit_period": 10})
