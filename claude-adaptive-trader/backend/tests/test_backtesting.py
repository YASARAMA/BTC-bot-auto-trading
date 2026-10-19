"""Backtester, walk-forward, metrics, statistics, gates, datasets and reports."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.backtesting import datasets
from app.backtesting.engine import END_OF_PERIOD, BacktestSpec, run_backtest
from app.backtesting.gates import NO_EDGE, PASSED, evaluate_gates
from app.backtesting.metrics import compute_metrics, drawdown, streaks, trades_frame
from app.backtesting.runner import BacktestRequest, FinalTestRequest, ResearchRunner, ValidationRequest
from app.backtesting.stats import bootstrap
from app.backtesting.walk_forward import make_windows
from app.config import Settings
from app.core.errors import Conflict, DataUnavailable, TestSetLocked
from app.research.experiment_tracker import ExperimentTracker
from tests.fakes import ScriptedStrategy, synthetic_candles

M15 = 900_000
S = Settings()


def candles(n: int = 3000, seed: int = 21) -> dict[str, pd.DataFrame]:
    df = synthetic_candles(n, "15m", start=1_700_000_000_000, drift=0.0002, vol=0.004, seed=seed)
    df["source"] = "test"
    return {"BTC/USDT": df}


def spec(c: dict[str, pd.DataFrame], start_i: int, end_i: int, **kw) -> BacktestSpec:
    ts = c["BTC/USDT"]["ts"]
    base = dict(pairs=["BTC/USDT"], timeframe="15m", confirm_timeframes=["1h", "4h"], exchange="binance",
                start=int(ts.iloc[start_i]), end=int(ts.iloc[end_i]), strategy="momentum_breakout")
    base.update(kw)
    return BacktestSpec(**base)


def test_backtests_are_reproducible() -> None:
    c = candles()
    a = run_backtest(spec(c, 900, 2999), c, S)
    b = run_backtest(spec(c, 900, 2999), c, S)
    assert [t.to_dict() for t in a.trades] == [t.to_dict() for t in b.trades]
    assert [e["equity"] for e in a.equity] == [e["equity"] for e in b.equity]


def test_data_after_the_period_cannot_change_the_result() -> None:
    c = candles()
    cut = {"BTC/USDT": c["BTC/USDT"].iloc[:2001].copy()}
    full = run_backtest(spec(c, 900, 2000), c, S)
    short = run_backtest(spec(cut, 900, 2000), cut, S)
    assert [t.to_dict() for t in full.trades] == [t.to_dict() for t in short.trades]
    assert full.equity[-1]["equity"] == pytest.approx(short.equity[-1]["equity"])


def scripted_run(mult: float) -> tuple[list, dict]:
    c = candles()
    strat = ScriptedStrategy(entries={i: (None, None) for i in []})
    df = c["BTC/USDT"]
    entries = {}
    for i in range(1000, 2900, 60):
        close = float(df["close"].iloc[i])
        entries[i] = (close * 0.98, close * 1.03)
    strat = ScriptedStrategy(entries=entries, time_stop=20)
    sp = spec(c, 950, 2999, strategy=None, cost_multiplier=mult)
    res = run_backtest(sp, c, S, strategies={strat.name: strat})
    return res.trades, compute_metrics(res.trades, res.equity, initial_cash=sp.initial_cash, benchmark=res.benchmark)


def test_higher_costs_never_help() -> None:
    t1, m1 = scripted_run(1.0)
    t2, m2 = scripted_run(2.0)
    t3, m3 = scripted_run(3.0)
    assert len(t1) == len(t2) == len(t3) > 10
    assert m1["net_return_pct"] > m2["net_return_pct"] > m3["net_return_pct"]
    assert m1["total_fees"] < m2["total_fees"] < m3["total_fees"]


def test_open_positions_are_closed_at_the_end_and_labelled() -> None:
    from app.strategies.pipeline import prepare

    c = candles()
    df = c["BTC/USDT"]
    regime = prepare(df, "BTC/USDT", "15m", ["1h", "4h"], S).regime["regime"]
    i = max(j for j in range(2970, 2998) if regime.iloc[j] != "uncertain")   # a candle the strategy may trade
    close = float(df["close"].iloc[i])
    strat = ScriptedStrategy(entries={i: (close * 0.97, close * 1.2)})
    res = run_backtest(spec(c, 950, 2999, strategy=None), c, S, strategies={strat.name: strat})
    assert res.trades[-1].exit_reason == END_OF_PERIOD
    assert any("closed at the last close" in n for n in res.notes)


def test_benchmark_is_buy_and_hold_with_costs() -> None:
    c = candles()
    res = run_backtest(spec(c, 950, 2999), c, S)
    df = c["BTC/USDT"]
    first_open = float(df["open"].iloc[950])
    last_close = float(df["close"].iloc[2999])
    gross = last_close / first_open
    assert res.benchmark[-1] / 10_000 == pytest.approx(gross, rel=0.01)
    assert res.benchmark[-1] / 10_000 < gross                    # costs were paid


def test_metrics_math() -> None:
    eq = [{"ts": 1_700_000_000_000 + i * 86_400_000, "equity": v, "cash": v, "exposure": 0.0}
          for i, v in enumerate([100, 110, 99, 120, 108])]
    dd, dur = drawdown(pd.Series([e["equity"] for e in eq], dtype=float))
    assert dd == pytest.approx(10.0) and dur == 1
    assert streaks([1, -1, -2, -3, 4, 5]) == (3, 2)
    trades = [{"net_pnl": 10, "gross_pnl": 11, "fees": 1, "slippage_cost": 0.5, "return_pct": 1.0, "r_multiple": 1.0,
               "exit_ts": 1, "entry_ts": 0, "regime": "trend_up", "environment": "4h up"},
              {"net_pnl": -5, "gross_pnl": -4, "fees": 1, "slippage_cost": 0.5, "return_pct": -0.5, "r_multiple": -0.5,
               "exit_ts": 2, "entry_ts": 1, "regime": "range", "environment": "4h flat"}]
    m = compute_metrics(trades, eq, initial_cash=100.0)
    assert m["win_rate_pct"] == 50.0 and m["profit_factor"] == 2.0 and m["expectancy"] == 2.5
    assert m["expectancy_r"] == 0.25 and m["net_without_best_trade"] == -5
    assert m["sharpe"] is None and any("fewer than 30" in n for n in m["notes"])
    assert set(m["by_regime"]) == {"trend_up", "range"}
    only_wins = compute_metrics(trades[:1], eq, initial_cash=100.0)
    assert only_wins["profit_factor"] is None and "profit factor is undefined" in only_wins["notes"][0]
    empty = compute_metrics([], [], initial_cash=100.0)
    assert empty["trades"] == 0 and empty["expectancy"] is None


def test_sharpe_defined_with_enough_days() -> None:
    rng = np.random.default_rng(1)
    vals = 1000 * np.cumprod(1 + rng.normal(0.001, 0.01, 120))
    eq = [{"ts": 1_700_000_000_000 + i * 86_400_000, "equity": float(v), "cash": float(v), "exposure": 1.0}
          for i, v in enumerate(vals)]
    m = compute_metrics([], eq, initial_cash=1000.0)
    assert m["sharpe"] is not None and m["sortino"] is not None and m["cagr_pct"] is not None


def test_bootstrap_is_seeded_and_needs_a_sample() -> None:
    df = trades_frame([{"net_pnl": v, "return_pct": v / 10, "r_multiple": v / 10} for v in
                       np.random.default_rng(3).normal(1, 5, 60)])
    a = bootstrap(df, samples=500, seed=7)
    b = bootstrap(df, samples=500, seed=7)
    assert a["expectancy_r"]["ci"] == b["expectancy_r"]["ci"]
    lo, hi = a["expectancy_r"]["ci"]
    assert lo < df["r_multiple"].mean() < hi
    assert bootstrap(df.head(5))["available"] is False


def good_oos() -> dict:
    return {"expectancy_r": 0.3, "profit_factor": 1.6, "max_drawdown_pct": 3.0, "trades": 400,
            "by_environment": {"4h up": {"trades": 200, "expectancy_r": 0.4}, "4h flat": {"trades": 150, "expectancy_r": 0.1}},
            "net_without_best_trade": 500.0, "best_month_share_pct": 20.0}


def test_gates_pass_only_when_everything_holds() -> None:
    boot = {"available": True, "expectancy_r": {"ci": (0.05, 0.5)}}
    ok = evaluate_gates(oos=good_oos(), stress={"expectancy_r": 0.1}, stability=0.8, window_ratio=0.8, bootstrap=boot,
                        cfg=S.gates, risk=S.risk)
    assert ok["verdict"] == PASSED and ok["failed"] == []
    bad = evaluate_gates(oos={**good_oos(), "trades": 40, "profit_factor": 1.1}, stress={"expectancy_r": -0.1},
                         stability=0.2, window_ratio=0.4, bootstrap={"available": False}, cfg=S.gates, risk=S.risk)
    assert bad["verdict"] == NO_EDGE
    assert set(bad["failed"]) >= {"sample_size", "profit_factor", "cost_stress", "parameter_stability",
                                  "walk_forward_consistency", "statistical_confidence"}
    assert "has not demonstrated a reliable edge" in bad["message"]


def test_walk_forward_windows_are_chronological_and_disjoint() -> None:
    start = 1_700_000_000_000 - 1_700_000_000_000 % M15
    end = start + 9_999 * M15
    ws = make_windows(start, end, "15m", 5, 0.7)
    assert len(ws) == 5 and ws[-1].test_end == end
    for w in ws:
        assert w.train_start <= w.train_end < w.test_start <= w.test_end
    for a, b in zip(ws, ws[1:]):
        assert a.test_end < b.test_start


@pytest.fixture
def research(services):
    df = synthetic_candles(4000, "15m", start=1_700_000_000_000, drift=0.0002, vol=0.004, seed=5)
    services.candle_store.upsert("binance", "BTC/USDT", "15m", df, "test")
    tracker = ExperimentTracker(services.db)
    return ResearchRunner(lambda: services.settings, services.db, services.market, tracker), tracker, services


def test_dataset_split_and_frozen_test_fingerprint(research) -> None:
    runner, tracker, svc = research
    ds = datasets.register(svc.db, svc.market, exchange="binance", pairs=["BTC/USDT"], timeframe="15m",
                           split=svc.settings.backtest.split)
    d0, d1 = ds.window("development")
    v0, v1 = ds.window("validation")
    t0, t1 = ds.window("test")
    assert d0 < d1 < v0 <= v1 < t0 <= t1 and ds.window("research") == (d0, v1)
    assert datasets.register(svc.db, svc.market, exchange="binance", pairs=["BTC/USDT"], timeframe="15m",
                             split=svc.settings.backtest.split).id == ds.id
    datasets.verify_test_unchanged(ds, svc.market)
    df = svc.candle_store.load("binance", "BTC/USDT", "15m", start=t0, end=t0)
    df.loc[:, "close"] = df["close"] * 1.0001
    df.loc[:, "high"] = df[["high", "close"]].max(axis=1)
    svc.candle_store.upsert("binance", "BTC/USDT", "15m", df, "tampered")
    with pytest.raises(TestSetLocked):
        datasets.verify_test_unchanged(ds, svc.market)


def test_backtest_experiment_is_recorded_with_metadata(research, tmp_path) -> None:
    runner, tracker, svc = research
    out = runner.backtest(BacktestRequest(strategy="momentum_breakout", pairs=["BTC/USDT"], segment="development"))
    exp = tracker.get(out["experiment_id"])
    assert exp["status"] == "completed" and exp["kind"] == "backtest" and not exp["uses_test_set"]
    assert exp["config_hash"] and exp["software_version"] and exp["dataset_id"]
    assert exp["strategies"][0].startswith("momentum_breakout@")
    assert set(exp["metrics"]) == {"base", "cost_2x", "cost_3x"}
    report = svc.settings.reports_path / out["experiment_id"] / "report.md"
    assert report.exists() and "Reproducibility" in report.read_text()
    assert (svc.settings.reports_path / out["experiment_id"] / "base_trades.csv").exists()


def test_research_requests_cannot_select_the_test_segment() -> None:
    with pytest.raises(ValidationError):
        BacktestRequest(strategy="momentum_breakout", segment="test")


def test_validation_records_a_verdict_and_final_test_is_guarded(research) -> None:
    runner, tracker, svc = research
    out = runner.validate(ValidationRequest(strategy="momentum_breakout", pairs=["BTC/USDT"]))
    assert out["verdict"] in {PASSED, NO_EDGE} and "failed_gates" in out
    version = tracker.version(out["version_id"])
    assert version["validation"]["verdict"] == out["verdict"]
    if out["verdict"] == NO_EDGE:
        assert version["status"] == "research"
        with pytest.raises(Conflict):
            runner.final_test(FinalTestRequest(version_id=version["id"], reason="checking the frozen test set",
                                               pairs=["BTC/USDT"]))
    assert svc.db.scalar("SELECT COUNT(*) FROM test_set_access") == 0


def test_final_test_is_logged(research) -> None:
    runner, tracker, svc = research
    v = tracker.ensure_version("momentum_breakout", {})
    svc.db.execute("UPDATE strategy_versions SET status = 'validated' WHERE id = ?", (v["id"],))
    out = runner.final_test(FinalTestRequest(version_id=v["id"], reason="final evaluation for the record",
                                             pairs=["BTC/USDT"]))
    exp = tracker.get(out["experiment_id"])
    assert exp["uses_test_set"] and exp["segment"] == "test"
    rows = svc.db.query("SELECT * FROM test_set_access")
    assert len(rows) == 1 and rows[0]["experiment_id"] == out["experiment_id"]
    assert out["test_set_accesses"] == 1


def test_failed_experiments_are_kept(research) -> None:
    runner, tracker, svc = research
    with pytest.raises(DataUnavailable):
        runner.backtest(BacktestRequest(strategy="momentum_breakout", pairs=["ETH/USDT"], segment="development"))
    # the dataset could not be registered, so nothing was created; a failure after creation is kept:
    eid = tracker.create(kind="backtest", settings=svc.settings, request={}, strategies=[], pairs=[], timeframe="15m",
                         segment="development")
    tracker.fail(eid, "RuntimeError: boom")
    assert tracker.get(eid)["status"] == "failed" and "boom" in tracker.get(eid)["error"]
