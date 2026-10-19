"""Fill simulation, portfolio accounting and position sizing."""
from __future__ import annotations

import math

import pytest

from app.config import CostConfig
from app.execution.fill_simulator import FillSimulator
from app.execution.models import Bar, Costs, FillResult, Order
from app.execution.portfolio import AccountingError, Portfolio
from app.risk.position_sizing import floor_step, size_position

COSTS = Costs.from_config(CostConfig(fee_pct_per_side=0.1, slippage_pct_per_side=0.05, spread_pct=0.02,
                                     take_profit_trade_through_pct=0.02, max_participation_pct=5.0,
                                     market_impact=False))


def bar(o=100.0, h=101.0, lo=99.0, c=100.5, v=1000.0, atr=1.0) -> Bar:
    return Bar("BTC/USDT", 0, o, h, lo, c, v, atr)


def test_cost_model_fractions() -> None:
    assert COSTS.fee == pytest.approx(0.001) and COSTS.slippage == pytest.approx(0.0005)
    assert COSTS.half_spread == pytest.approx(0.0001)
    assert COSTS.round_trip_pct == pytest.approx(0.32)
    double = Costs.from_config(CostConfig(), multiplier=2.0)
    assert double.round_trip_pct == pytest.approx(0.64)


def test_market_buy_pays_slippage_spread_and_fee() -> None:
    f = FillSimulator(COSTS).market("buy", bar(o=100.0), 2.0)
    assert f.price == pytest.approx(100.0 * 1.0006)
    assert f.fee == pytest.approx(2.0 * f.price * 0.001)
    assert f.slippage_cost == pytest.approx(2.0 * 0.06)
    assert not f.partial and f.rejected is None


def test_market_sell_is_symmetric() -> None:
    f = FillSimulator(COSTS).market("sell", bar(o=100.0), 1.0)
    assert f.price == pytest.approx(100.0 * (1 - 0.0006))


def test_partial_fill_capped_by_volume() -> None:
    f = FillSimulator(COSTS).market("buy", bar(v=100.0), 10.0)
    assert f.filled_qty == pytest.approx(5.0) and f.partial          # 5% of 100


def test_no_volume_no_fill() -> None:
    f = FillSimulator(COSTS).market("buy", bar(v=0.0), 1.0)
    assert f.rejected and f.filled_qty == 0


def test_market_impact_grows_with_participation() -> None:
    costs = Costs.from_config(CostConfig(market_impact=True))
    sim = FillSimulator(costs)
    small = sim.market("buy", bar(v=10_000.0, atr=2.0), 1.0)
    large = sim.market("buy", bar(v=10_000.0, atr=2.0), 400.0)
    assert large.price > small.price > 100.0 * (1 + costs.slippage + costs.half_spread)


def test_stop_fills_at_stop_or_at_a_gap() -> None:
    sim = FillSimulator(COSTS)
    assert sim.stop_triggered(bar(o=100, lo=97.5), 98.0) == (98.0, False)
    assert sim.stop_triggered(bar(o=96.0, h=97, lo=95), 98.0) == (96.0, True)     # gapped: the open, not the stop
    assert sim.stop_triggered(bar(o=100, lo=98.5), 98.0) is None


def test_take_profit_needs_a_trade_through() -> None:
    sim = FillSimulator(COSTS)
    assert not sim.target_filled(bar(h=105.0), 105.0)                # a touch is not a fill
    assert not sim.target_filled(bar(h=105.0 * 1.0001), 105.0)
    assert sim.target_filled(bar(h=105.0 * 1.0003), 105.0)
    fill = sim.limit_sell(bar(h=106), 1.0, 105.0)
    assert fill.price == 105.0 and fill.slippage_cost == 0.0         # never better than the limit


def order(qty: float = 1.0, stop: float = 95.0, target: float | None = 110.0) -> Order:
    return Order(client_order_id="o1", pair="BTC/USDT", side="buy", purpose="entry", reason="test", created_ts=0,
                 signal_ts=0, qty=qty, ref_price=100.0, strategy="s", strategy_version="s@1", regime="trend_up",
                 stop=stop, target=target)


def test_portfolio_identity_through_a_round_trip() -> None:
    pf = Portfolio.new(10_000.0)
    entry = FillResult(2.0, 100.06, 100.0, 0.20012, 0.12, 0, False)
    pf.open_position(order(2.0), entry, initial_risk=10.0, key="k1")
    marks = {"BTC/USDT": 103.0}
    pf.check_invariants(marks)
    assert pf.cash == pytest.approx(10_000 - 2 * 100.06 - 0.20012)
    assert pf.unrealized_pnl(marks) == pytest.approx(2 * (103 - 100.06) - 0.20012)
    exit_fill = FillResult(2.0, 104.0, 104.06, 0.208, 0.12, 0, False)
    trade = pf.reduce_position("BTC/USDT", exit_fill, 1, "take profit")
    assert trade is not None and not pf.positions
    assert trade.gross_pnl == pytest.approx(2 * (104.0 - 100.06))
    assert trade.fees == pytest.approx(0.20012 + 0.208)
    assert trade.net_pnl == pytest.approx(trade.gross_pnl - trade.fees)
    assert pf.cash == pytest.approx(10_000 + trade.net_pnl)
    assert trade.r_multiple == pytest.approx(trade.net_pnl / 10.0)
    pf.check_invariants({})


def test_partial_exit_then_close() -> None:
    pf = Portfolio.new(1_000.0)
    pf.open_position(order(4.0), FillResult(4.0, 100.0, 100.0, 0.4, 0, 0, False), initial_risk=20, key="k")
    assert pf.reduce_position("BTC/USDT", FillResult(1.0, 99.0, 99.0, 0.099, 0, 0, True), 1, "stop") is None
    assert pf.positions["BTC/USDT"].remaining == pytest.approx(3.0)
    pf.check_invariants({"BTC/USDT": 98.0})
    trade = pf.reduce_position("BTC/USDT", FillResult(3.0, 98.0, 98.0, 0.294, 0, 0, False), 2, "stop")
    assert trade.exit_price == pytest.approx((99 + 3 * 98) / 4)
    assert trade.net_pnl == pytest.approx((99 - 100) + 3 * (98 - 100) - 0.4 - 0.099 - 0.294)


def test_portfolio_refuses_overspending_and_pyramiding() -> None:
    pf = Portfolio.new(100.0)
    with pytest.raises(AccountingError):
        pf.open_position(order(2.0), FillResult(2.0, 100.0, 100.0, 0.2, 0, 0, False), initial_risk=1, key="a")
    pf.open_position(order(0.5), FillResult(0.5, 100.0, 100.0, 0.05, 0, 0, False), initial_risk=1, key="a")
    with pytest.raises(AccountingError):
        pf.open_position(order(0.1), FillResult(0.1, 100.0, 100.0, 0.01, 0, 0, False), initial_risk=1, key="b")


def sizing(**kw):
    base = dict(equity=10_000.0, cash=10_000.0, entry_price=100.0, stop_price=98.0, risk_pct=0.25, max_risk_pct=0.5,
                open_risk=0.0, max_open_risk_pct=1.0, max_position_pct=20.0, exposure=0.0, max_total_exposure_pct=60.0,
                costs=COSTS, avg_bar_volume=None, liquidity_max_volume_pct=2.0, amount_step=0.001, min_qty=0.001,
                min_notional=10.0)
    base.update(kw)
    return size_position(**base)


def test_risk_based_size_counts_costs() -> None:
    r = sizing()
    entry_est = 100.0 * 1.0006
    per_unit = entry_est * 1.001 - 98.0 * (1 - 0.0006) * (1 - 0.001)
    assert r.per_unit_loss == pytest.approx(per_unit)
    assert r.binding == "risk"
    assert r.qty == floor_step(25.0 / per_unit, 0.001)
    assert r.risk_amount <= 25.0 + 1e-9                                 # never more than 0.25% of equity


@pytest.mark.parametrize("kw, binding", [
    ({"cash": 500.0}, "cash"),
    ({"avg_bar_volume": 100.0}, "liquidity"),
    ({"max_position_pct": 1.0}, "position_limit"),
    ({"exposure": 5_950.0}, "total_exposure"),
    ({"open_risk": 90.0}, "risk"),
])
def test_final_size_is_the_smallest_constraint(kw, binding) -> None:
    r = sizing(**kw)
    assert r.rejected is None and r.binding == binding
    assert r.qty == pytest.approx(min(v for v in r.candidates.values() if math.isfinite(v)), abs=0.001)


def test_open_risk_budget_exhausted_rejects() -> None:
    assert sizing(open_risk=100.0).rejected == "the aggregate open-risk budget is already used"


def test_max_risk_caps_configured_risk() -> None:
    assert sizing(risk_pct=5.0, max_risk_pct=0.5, max_open_risk_pct=3.0).risk_amount <= 50.0 + 1e-9


def test_minimums_and_rounding() -> None:
    assert "below the minimum" in sizing(cash=5.0).rejected
    assert floor_step(0.123456, 0.001) == 0.123
    assert floor_step(1.0, None) == 1.0
    assert "below the exchange minimum" in sizing(min_qty=1000.0, amount_step=None).rejected


def test_invalid_stop_rejected() -> None:
    assert sizing(stop_price=101.0).rejected and sizing(stop_price=0.0).rejected
