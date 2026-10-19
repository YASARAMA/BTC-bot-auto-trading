"""The trading core end to end on scripted signals: timing, stops, targets, gaps, partials."""
from __future__ import annotations

import pandas as pd
import pytest

from app.config import CostConfig, Settings
from app.execution.core import PairState, TradingCore
from app.execution.models import Costs
from app.execution.portfolio import Portfolio
from app.execution.recorders import MemoryRecorder
from app.risk import circuit_breakers as cb
from app.risk.risk_manager import EngineHealth, RiskManager
from app.strategies.base import StrategyContext
from app.strategies.pipeline import MarketView
from app.strategies.selector import FixedSelector
from tests.fakes import ScriptedStrategy, bars_frame

S = Settings()
COSTS = Costs.from_config(CostConfig(market_impact=False))
CTX = StrategyContext(round_trip_cost_pct=0.32, confirm_timeframes=("1h", "4h"))
FLAT = (100.0, 100.5, 99.5, 100.0, 1000.0)


def state(df: pd.DataFrame, strat: ScriptedStrategy) -> PairState:
    n = len(df)
    regime = pd.DataFrame({"regime": ["trend_up"] * n, "regime_confidence": [0.8] * n, "regime_reason": [""] * n,
                           "regime_direction": [1] * n})
    view = MarketView(pair="BTC/USDT", timeframe="15m", features=df.reset_index(drop=True), regime=regime)
    return PairState("BTC/USDT", view, {strat.name: strat.signals(view.features, regime, CTX)},
                     {"amount_step": 0.0001, "min_qty": 0.0001, "min_notional": 5.0, "source": "test"})


def core_for(strat: ScriptedStrategy, cash: float = 10_000.0, health=None) -> tuple[TradingCore, MemoryRecorder]:
    rec = MemoryRecorder()
    core = TradingCore(run_id="t", timeframe="15m", confirm_timeframes=["1h", "4h"], costs=COSTS, risk_cfg=S.risk,
                       risk=RiskManager(S.risk, COSTS), selector=FixedSelector(strat), strategies={strat.name: strat},
                       portfolio=Portfolio.new(cash), breakers=cb.new_state(cash, 0), recorder=rec, health=health)
    return core, rec


def run(core: TradingCore, ps: PairState) -> None:
    for i in range(len(ps.ts)):
        core.step([(ps, i)])
        core.portfolio.check_invariants(core.marks)


def test_signal_at_close_fills_at_next_open_then_take_profit() -> None:
    rows = [FLAT] * 10
    rows[6] = (100.2, 100.8, 99.9, 100.6, 1000.0)
    rows[8] = (101.0, 104.5, 100.9, 104.2, 1000.0)            # trades through the 104 target
    strat = ScriptedStrategy(entries={5: (98.0, 104.0)})
    core, rec = core_for(strat)
    ps = state(bars_frame(rows), strat)
    run(core, ps)
    assert len(rec.trades) == 1
    t = rec.trades[0]
    assert t.entry_ts == ps.ts[6]                              # not the signal candle
    assert t.entry_price == pytest.approx(100.2 * (1 + COSTS.slippage + COSTS.half_spread))
    assert t.exit_ts == ps.ts[8] and t.exit_price == 104.0 and t.exit_reason == "take profit"
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.fees) and t.net_pnl > 0
    assert rec.decisions[0]["action"] == "enter" and rec.decisions[0]["accepted"]
    assert len(rec.decisions[0]["checks"]) == 14
    assert len(rec.equity_points) == 10


def test_stop_is_assumed_first_when_both_are_reached() -> None:
    rows = [FLAT] * 10
    rows[7] = (100.0, 105.0, 97.0, 101.0, 1000.0)             # reaches target and stop in one candle
    strat = ScriptedStrategy(entries={5: (98.0, 104.0)})
    core, rec = core_for(strat)
    run(core, state(bars_frame(rows), strat))
    t = rec.trades[0]
    assert t.exit_reason == "stop loss" and t.net_pnl < 0
    assert t.exit_price == pytest.approx(98.0 * (1 - COSTS.slippage - COSTS.half_spread))


def test_gap_through_the_stop_fills_at_the_open() -> None:
    rows = [FLAT] * 10
    rows[8] = (95.0, 95.5, 94.0, 95.2, 1000.0)                # opens far below the 98 stop
    strat = ScriptedStrategy(entries={5: (98.0, 110.0)})
    core, rec = core_for(strat)
    run(core, state(bars_frame(rows), strat))
    t = rec.trades[0]
    assert "gapped" in t.exit_reason
    assert t.exit_price == pytest.approx(95.0 * (1 - COSTS.slippage - COSTS.half_spread))
    assert core.stats.stop_gaps == 1
    assert abs(t.net_pnl) > t.initial_risk                     # a stop does not guarantee its price


def test_touching_the_target_is_not_enough() -> None:
    rows = [FLAT] * 10
    rows[7] = (100.0, 104.0, 99.8, 103.0, 1000.0)             # exactly touches 104
    strat = ScriptedStrategy(entries={5: (98.0, 104.0)})
    core, rec = core_for(strat)
    run(core, state(bars_frame(rows), strat))
    assert rec.trades == [] and "BTC/USDT" in core.portfolio.positions


def test_time_stop_and_strategy_exit_execute_at_the_next_open() -> None:
    rows = [FLAT] * 14
    strat = ScriptedStrategy(entries={2: (98.0, 110.0)}, time_stop=3)
    core, rec = core_for(strat)
    ps = state(bars_frame(rows), strat)
    run(core, ps)
    t = rec.trades[0]
    assert t.exit_reason.startswith("time stop") and t.bars_held == 3
    assert t.exit_ts == ps.ts[3 + 3]                           # decided at the close of the 3rd candle held
    strat2 = ScriptedStrategy(entries={2: (98.0, 110.0)}, exits={4})
    core2, rec2 = core_for(strat2)
    ps2 = state(bars_frame(rows), strat2)
    run(core2, ps2)
    assert rec2.trades[0].exit_reason == "scripted exit" and rec2.trades[0].exit_ts == ps2.ts[5]


def test_invalidation_level_exits_on_close() -> None:
    rows = [FLAT] * 10
    rows[6] = (100.0, 100.2, 99.2, 99.3, 1000.0)               # closes below the 99.5 invalidation
    strat = ScriptedStrategy(entries={3: (97.0, 110.0)}, invalidation={3: 99.5})
    core, rec = core_for(strat)
    ps = state(bars_frame(rows), strat)
    run(core, ps)
    assert rec.trades[0].exit_reason.startswith("invalidation") and rec.trades[0].exit_ts == ps.ts[7]


def test_missing_execution_candle_cancels_the_entry() -> None:
    df = bars_frame([FLAT] * 10).drop(index=[6]).reset_index(drop=True)   # the candle after the signal is missing
    strat = ScriptedStrategy(entries={5: (98.0, 110.0)})
    core, rec = core_for(strat)
    run(core, state(df, strat))
    assert rec.trades == [] and not core.portfolio.positions
    assert core.stats.gap_cancellations == 1
    assert rec.orders[0]["event"] == "cancelled" and "data gap" in rec.orders[0]["note"]


def test_partial_entry_fill_on_thin_volume() -> None:
    rows = [FLAT] * 10
    rows[6] = (100.0, 100.5, 99.5, 100.0, 4.0)                 # 5% of 4 units = 0.2 fillable
    strat = ScriptedStrategy(entries={5: (98.0, 110.0)})
    core, rec = core_for(strat)
    run(core, state(bars_frame(rows), strat))
    pos = core.portfolio.positions["BTC/USDT"]
    assert pos.qty == pytest.approx(0.2) and core.stats.partial_fills == 1


def test_gap_up_open_buys_only_what_cash_allows() -> None:
    rows = [FLAT] * 10
    rows[6] = (300.0, 301.0, 299.0, 300.0, 1000.0)             # opens 3x above the decision price
    strat = ScriptedStrategy(entries={5: (98.0, 400.0)})
    core, rec = core_for(strat, cash=1_000.0)
    run(core, state(bars_frame(rows), strat))
    assert core.portfolio.cash >= 0
    core.portfolio.check_invariants(core.marks)


def test_kill_switch_blocks_new_entries() -> None:
    strat = ScriptedStrategy(entries={5: (98.0, 110.0)})
    core, rec = core_for(strat, health=lambda: EngineHealth(False, ("kill switch is on",)))
    run(core, state(bars_frame([FLAT] * 10), strat))
    assert not core.portfolio.positions and rec.trades == []
    assert core.stats.rejections["execution_engine_health"] == 1
    assert rec.decisions[0]["accepted"] is False and "kill switch" in rec.decisions[0]["reason"]


def test_no_new_entry_while_a_position_is_open() -> None:
    strat = ScriptedStrategy(entries={2: (98.0, 110.0), 4: (98.0, 110.0)})
    core, rec = core_for(strat)
    run(core, state(bars_frame([FLAT] * 10), strat))
    assert core.stats.decisions["enter"] == 1                  # the second signal is not even evaluated
    assert len(core.portfolio.positions) == 1


def test_equity_curve_and_identity_hold_at_every_step() -> None:
    rows = [(100 + i * 0.3, 100.6 + i * 0.3, 99.6 + i * 0.3, 100.3 + i * 0.3, 1000.0) for i in range(30)]
    strat = ScriptedStrategy(entries={3: (97.0, 120.0), 20: (100.0, 130.0)}, exits={15})
    core, rec = core_for(strat)
    run(core, state(bars_frame(rows), strat))
    assert len(rec.equity_points) == 30
    final = rec.equity_points[-1]
    assert final["equity"] == pytest.approx(core.portfolio.initial_cash + core.portfolio.realized_pnl
                                            + core.portfolio.unrealized_pnl(core.marks))
