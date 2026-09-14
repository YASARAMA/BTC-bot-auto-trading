"""Monte Carlo over the trade sequence, and parameter sensitivity."""
from __future__ import annotations

import pandas as pd
import pytest

from bot.backtest.robustness import (
    _path,
    _shape,
    monte_carlo,
    sensitivity,
    trade_returns,
)
from bot.models import Trade
from tests.conftest import TF_MS, T0, make_ohlcv, trending_series


def trade(pnl: float, entry_ts: int, exit_ts: int | None = None) -> Trade:
    return Trade(symbol="BTC/USDT", strategy="t", qty=1.0, entry_ts=entry_ts, entry_price=100.0,
                 exit_ts=exit_ts or entry_ts + TF_MS, exit_price=100.0 + pnl, fees=0.0, pnl=pnl,
                 pnl_pct=pnl, exit_reason="signal", entry_order_id="a", exit_order_id="b")


def equity_frame(values: list[float], start: int = T0) -> pd.DataFrame:
    return pd.DataFrame({"ts": [start + i * TF_MS for i in range(len(values))],
                         "equity": values, "position_qty": [0.0] * len(values)})


def test_trade_returns_are_fractions_of_the_account_at_entry():
    eq = equity_frame([1000.0, 1000.0, 1100.0, 1100.0])
    trades = [trade(100.0, T0), trade(-110.0, T0 + 2 * TF_MS)]
    assert trade_returns(trades, eq) == pytest.approx([0.1, -0.1])
    assert trade_returns([], eq) == []
    assert trade_returns(trades, pd.DataFrame()) == []


def test_compounding_a_path_reports_final_and_worst_drawdown():
    final, dd = _path([0.5, -0.5])
    assert final == pytest.approx(0.75) and dd == pytest.approx(50.0)
    final, dd = _path([0.1, 0.1])
    assert final == pytest.approx(1.21) and dd == pytest.approx(0.0)
    # A loss of everything is a wipeout, not a negative account.
    assert _path([-1.5]) == (0.0, 100.0)


def test_shuffling_keeps_the_return_and_only_moves_the_drawdown():
    eq = equity_frame([1000.0] * 6)
    trades = [trade(p, T0 + i * TF_MS) for i, p in enumerate([200.0, -100.0, 150.0, -50.0, 300.0, -120.0])]
    mc = monte_carlo(trades, eq, method="shuffle", runs=200, seed=3)
    # Compounding is commutative, so every ordering ends at the same number...
    assert mc.returns_pct["p05"] == pytest.approx(mc.returns_pct["p95"], abs=1e-6)
    assert mc.returns_pct["median"] == pytest.approx(mc.actual_return_pct, abs=1e-6)
    # ...while the worst drawdown along the way depends entirely on the order.
    assert mc.drawdowns_pct["worst"] > mc.drawdowns_pct["median"]
    assert mc.trades == 6 and mc.runs == 200


def test_resampling_spreads_the_outcomes_and_is_reproducible():
    eq = equity_frame([1000.0] * 12)
    trades = [trade(p, T0 + i * TF_MS) for i, p in enumerate([80.0, -50.0, 60.0, -40.0, 90.0, -30.0,
                                                             70.0, -60.0, 40.0, -20.0])]
    a = monte_carlo(trades, eq, method="resample", runs=300, seed=11)
    b = monte_carlo(trades, eq, method="resample", runs=300, seed=11)
    assert a.to_dict() == b.to_dict(), "the same seed must give the same answer"
    assert a.returns_pct["p05"] < a.returns_pct["median"] < a.returns_pct["p95"]
    assert 0.0 <= a.prob_loss_pct <= 100.0
    different = monte_carlo(trades, eq, method="resample", runs=300, seed=12)
    assert different.returns_pct["median"] != a.returns_pct["median"] or different.runs == a.runs


def test_a_losing_strategy_is_called_out():
    eq = equity_frame([1000.0] * 12)
    trades = [trade(p, T0 + i * TF_MS) for i, p in enumerate([-80.0] * 5 + [40.0] * 5)]
    mc = monte_carlo(trades, eq, runs=300, seed=5)
    assert mc.prob_loss_pct > 50.0
    assert "do not trade it with real money" in mc.verdict or "not profit" in mc.verdict


def test_risk_of_ruin_is_reported_when_the_account_can_be_halved():
    eq = equity_frame([1000.0] * 10)
    trades = [trade(p, T0 + i * TF_MS) for i, p in enumerate([-300.0, -300.0, 900.0, -300.0, 600.0])]
    mc = monte_carlo(trades, eq, runs=400, seed=2)
    assert mc.prob_ruin_pct > 0.0
    assert "risk of ruin" in mc.verdict


def test_monte_carlo_needs_trades_and_a_known_method():
    eq = equity_frame([1000.0, 1000.0])
    with pytest.raises(ValueError, match="at least 2 trades"):
        monte_carlo([trade(10.0, T0)], eq)
    with pytest.raises(ValueError, match="resample"):
        monte_carlo([trade(10.0, T0), trade(5.0, T0 + TF_MS)], eq, method="magic")


def test_small_samples_are_flagged_as_a_rough_sketch():
    eq = equity_frame([1000.0] * 5)
    trades = [trade(p, T0 + i * TF_MS) for i, p in enumerate([50.0, -20.0, 30.0])]
    assert "rough sketch" in monte_carlo(trades, eq, runs=100).verdict


def test_shape_tells_a_spike_from_a_plateau():
    assert _shape([1.0, 1.1, 1.05], 1)[0] == "plateau"
    shape, drop = _shape([0.1, 2.0, 0.1], 1)
    assert shape == "spike" and drop is not None and drop > 50
    assert _shape([1.0, 1.0, 1.0], 0)[0] == "flat"
    assert _shape([None, None, 1.0], 2)[0] == "too few points"
    assert _shape([-1.0, -2.0, -3.0], 0)[0] == "no profitable setting"
    # One neighbour is enough to judge the shape, even when the best value is at the end:
    # halving the score in a single step is a spike wherever it sits.
    assert _shape([1.0, 0.5, 0.2], 0)[0] == "spike"
    assert _shape([1.0, 0.95, 0.9], 0)[0] == "plateau"


def test_sensitivity_sweeps_one_parameter_at_a_time(cfg):
    df = make_ohlcv(trending_series(n_down=200, n_up=260, n_down2=200), spread=0.001)
    grid = {"entry_period": [10, 20, 30], "atr_stop_mult": [1.5, 2.0, 3.0]}
    out = sensitivity(df, cfg, strategy_name="breakout", params={"exit_period": 10},
                      grid=grid, min_trades=1, workers=1)
    assert out.strategy == "breakout" and len(out.sweeps) == 2
    by_name = {s.param: s for s in out.sweeps}
    assert by_name["entry_period"].values == [10, 20, 30]
    assert len(by_name["entry_period"].scores) == 3
    assert all(t >= 0 for t in by_name["entry_period"].trades)
    assert out.verdict
    payload = out.to_dict()
    assert payload["sweeps"][0]["param"] == "entry_period" and "at_edge" in payload["sweeps"][0]


def test_sensitivity_reports_when_nothing_is_profitable(cfg):
    df = make_ohlcv([100 * 0.995 ** i for i in range(400)])  # a market that only falls
    out = sensitivity(df, cfg, strategy_name="breakout", grid={"entry_period": [10, 20, 30]},
                      min_trades=1, workers=1)
    assert out.sweeps[0].shape in ("no profitable setting", "no trades", "too few points")
    assert "not the tuning" in out.verdict or "produced a single trade" in out.verdict


def test_sensitivity_refuses_an_empty_search_space(cfg):
    df = make_ohlcv(trending_series())
    with pytest.raises(ValueError, match="no parameters to sweep"):
        sensitivity(df, cfg, strategy_name="breakout", grid={}, workers=1)
