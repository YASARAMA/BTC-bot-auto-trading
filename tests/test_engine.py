import pandas as pd
import pytest

from bot.engine import TradingEngine
from bot.execution.executor import OrderExecutor
from bot.execution.paper import PaperExchange
from bot.models import Action, Signal
from bot.notify.notifier import Notifier
from bot.risk.manager import RiskManager
from bot.state.store import StateStore
from tests.conftest import TF_MS, ScriptedStrategy, buy_signal, make_ohlcv


def build(cfg, store, strategy, clock, cash=10_000.0):
    exchange = PaperExchange(symbol="BTC/USDT", fee_rate=cfg.exchange.fee_rate, slippage_bps=0.0,
                             initial_cash=cash, clock=clock)
    risk = RiskManager(cfg.risk, fee_rate=cfg.exchange.fee_rate)
    executor = OrderExecutor(exchange, store, symbol="BTC/USDT", strategy_name=strategy.name,
                             order_timeout_seconds=0.0, order_poll_seconds=0.0, sleep=lambda _s: None, clock=clock)
    engine = TradingEngine(cfg=cfg, strategy=strategy, risk=risk, exchange=exchange, executor=executor,
                           store=store, notifier=Notifier(cfg.notify), clock=clock, mode="test")
    return engine, exchange


def test_entry_then_stop_loss_then_persisted_restart(cfg, tmp_path):
    closes = [100.0] * 5 + [100.0, 97.0, 97.5]  # candle 6 falls through the 2% stop
    df = make_ohlcv(closes, spread=0.0)
    ts = df["ts"].tolist()
    strategy = ScriptedStrategy({ts[4]: buy_signal(100.0)})
    clock = {"now": ts[0]}
    store = StateStore(tmp_path / "state.sqlite")
    engine, exchange = build(cfg, store, strategy, lambda: clock["now"])

    for i in range(5):
        clock["now"] = ts[i] + TF_MS
        exchange.set_price(float(df["open"].iloc[i + 1]))
        out = engine.process_candle(df.iloc[: i + 1], fill_price=float(df["open"].iloc[i + 1]), exit_at_level=True)
    assert engine.position is not None and out["order"]["side"] == "buy"
    qty = engine.position.qty
    assert qty == pytest.approx(min(10_000 * 0.01 / 2.0, 2_500 / 100.0))  # risk-based 50 vs cap 25 -> 25 BTC
    assert engine.position.stop_loss == pytest.approx(98.0)

    # Idempotent: the same candle again is skipped and does not re-enter.
    again = engine.process_candle(df.iloc[:5], fill_price=100.0, exit_at_level=True)
    assert "skipped" in again and engine.position.qty == qty

    # Restart: a new engine on the same store restores position, risk state and last candle.
    engine2, exchange2 = build(cfg, StateStore(tmp_path / "state.sqlite"), ScriptedStrategy(), lambda: clock["now"])
    assert engine2.position is not None and engine2.position.qty == pytest.approx(qty)
    assert engine2.last_candle_ts == ts[4]
    assert exchange2.fetch_balance()["BTC"]["total"] == pytest.approx(qty)
    assert engine2.risk.state.trades_today == 1

    # Candle 6 trades down to 97: the stop at 98 fills at its level (no gap through it).
    clock["now"] = ts[6] + TF_MS
    exchange2.set_price(97.5)
    out = engine2.process_candle(df.iloc[:7], fill_price=97.5, exit_at_level=True)
    assert engine2.position is None
    assert out["protective"]["intent"]["kind"] == "stop_loss"
    trades = engine2.store.trades()
    assert len(trades) == 1 and trades[0].exit_reason == "stop_loss"
    assert trades[0].exit_price == pytest.approx(98.0)
    assert trades[0].pnl < 0
    assert engine2.risk.state.consecutive_losses == 1


def test_gap_through_stop_fills_at_open(cfg):
    closes = [100.0] * 5 + [100.0, 90.0]
    df = make_ohlcv(closes, spread=0.0)
    df.loc[6, "open"] = 92.0  # opens below the 98 stop
    ts = df["ts"].tolist()
    strategy = ScriptedStrategy({ts[4]: buy_signal(100.0)})
    clock = {"now": ts[0]}
    engine, exchange = build(cfg, StateStore(":memory:"), strategy, lambda: clock["now"])
    for i in range(7):
        clock["now"] = ts[i] + TF_MS
        nxt = float(df["open"].iloc[i + 1]) if i + 1 < len(df) else float(df["close"].iloc[i])
        exchange.set_price(nxt)
        engine.process_candle(df.iloc[: i + 1], fill_price=nxt, exit_at_level=True)
    t = engine.store.trades()
    assert len(t) == 1 and t[0].exit_reason == "stop_loss" and t[0].exit_price == pytest.approx(92.0)


def test_take_profit_and_tick_exit(cfg):
    closes = [100.0] * 6
    df = make_ohlcv(closes, spread=0.0)
    ts = df["ts"].tolist()
    strategy = ScriptedStrategy({ts[4]: buy_signal(100.0, tp_pct=0.04)})
    clock = {"now": ts[0]}
    engine, exchange = build(cfg, StateStore(":memory:"), strategy, lambda: clock["now"])
    for i in range(5):
        clock["now"] = ts[i] + TF_MS
        exchange.set_price(100.0)
        engine.process_candle(df.iloc[: i + 1], fill_price=100.0)
    assert engine.position is not None
    clock["now"] = ts[5] + 600_000
    exchange.set_price(104.5)
    out = engine.process_tick(104.5)
    assert out and out["intent"]["kind"] == "take_profit"
    assert engine.position is None
    t = engine.store.trades()[0]
    assert t.exit_reason == "take_profit" and t.exit_price == pytest.approx(104.5) and t.pnl > 0


def test_sell_signal_closes_position_and_records_trade(cfg):
    closes = [100.0] * 8
    df = make_ohlcv(closes, spread=0.0)
    ts = df["ts"].tolist()
    strategy = ScriptedStrategy({ts[4]: buy_signal(100.0), ts[6]: Signal(Action.SELL, 1.0, "cross down")})
    clock = {"now": ts[0]}
    engine, exchange = build(cfg, StateStore(":memory:"), strategy, lambda: clock["now"])
    for i in range(8):
        clock["now"] = ts[i] + TF_MS
        exchange.set_price(100.0)
        engine.process_candle(df.iloc[: i + 1], fill_price=100.0)
    assert engine.position is None
    t = engine.store.trades()
    assert len(t) == 1 and t[0].exit_reason == "exit"
    assert t[0].pnl == pytest.approx(-t[0].fees)  # flat price: only fees are lost
    curve = engine.store.equity_curve()
    assert len(curve) == 8 and curve[-1]["position_qty"] == 0.0


def test_reconcile_clears_phantom_position(cfg):
    df = make_ohlcv([100.0] * 3, spread=0.0)
    clock = {"now": int(df["ts"].iloc[0])}
    store = StateStore(":memory:")
    store.set_state("position", {"qty": 1.0, "entry_price": 100.0, "entry_ts": 1, "stop_loss": 90.0,
                                 "take_profit": 110.0, "entry_order_id": "bot" + "0" * 24, "strategy": "x"})
    engine, exchange = build(cfg, store, ScriptedStrategy(), lambda: clock["now"])
    exchange.set_price(100.0)
    assert engine.position is not None
    summary = engine.reconcile()
    assert engine.position is None
    assert any("clearing position" in w for w in summary["warnings"])
    assert store.get_state("position") is None
