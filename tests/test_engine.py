from pathlib import Path

import pandas as pd
import pytest

from bot.engine import TradingEngine
from bot.execution.executor import OrderExecutor
from bot.execution.paper import PaperExchange
from bot.models import Action, Side, Signal
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
    assert out and out["protective"]["intent"]["kind"] == "take_profit"
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


def test_manual_buy_and_sell(cfg, tmp_path):
    df = make_ohlcv([100.0] * 6, spread=0.0)
    ts = df["ts"].tolist()
    clock = {"now": ts[0] + TF_MS}
    engine, exchange = build(cfg, StateStore(":memory:"), ScriptedStrategy(), lambda: clock["now"])
    exchange.set_price(100.0)

    out = engine.manual_order(Side.BUY, quote_amount=2000.0, stop_loss=95.0, take_profit=110.0)
    assert engine.position is not None
    assert engine.position.qty == pytest.approx(20.0, rel=1e-3)
    assert engine.position.stop_loss == 95.0 and engine.position.take_profit == 110.0
    assert out["order"]["side"] == "buy" and out["order"]["kind"] == "entry"
    assert engine.risk.state.trades_today == 1  # counted like any other entry

    # A second buy is refused; the bot is already long.
    with pytest.raises(ValueError, match="already in a position"):
        engine.manual_order(Side.BUY, quote_amount=100.0)

    # Levels can be moved afterwards.
    engine.set_protective_levels(96.0, 120.0)
    assert engine.position.stop_loss == 96.0 and engine.position.take_profit == 120.0
    with pytest.raises(ValueError, match="below the current price"):
        engine.set_protective_levels(101.0, 120.0)

    exchange.set_price(105.0)
    engine.manual_order(Side.SELL)
    assert engine.position is None
    trades = engine.store.trades()
    assert len(trades) == 1 and trades[0].exit_reason == "exit" and trades[0].pnl > 0
    assert trades[0].exit_price == pytest.approx(105.0)


def test_manual_order_validation_and_kill_switch(cfg, tmp_path):
    df = make_ohlcv([100.0] * 4, spread=0.0)
    clock = {"now": int(df["ts"].iloc[0]) + TF_MS}
    engine, exchange = build(cfg, StateStore(":memory:"), ScriptedStrategy(), lambda: clock["now"], cash=500.0)
    exchange.set_price(100.0)

    with pytest.raises(ValueError, match="no open position"):
        engine.manual_order(Side.SELL)
    with pytest.raises(ValueError, match="give either qty or quote_amount"):
        engine.manual_order(Side.BUY)
    with pytest.raises(ValueError, match="stop loss"):
        engine.manual_order(Side.BUY, quote_amount=100.0, stop_loss=150.0)
    with pytest.raises(ValueError, match="take profit"):
        engine.manual_order(Side.BUY, quote_amount=100.0, take_profit=50.0)
    with pytest.raises(ValueError, match="too small"):
        engine.manual_order(Side.BUY, quote_amount=2.0)

    # Spending more than the balance is capped at what is actually affordable.
    engine.manual_order(Side.BUY, quote_amount=10_000.0)
    assert engine.position.qty * 100.0 <= 500.0
    engine.manual_order(Side.SELL)

    # Kill switch blocks manual orders too.
    Path(cfg.risk.kill_switch_file).write_text("stop")
    with pytest.raises(PermissionError, match="kill switch"):
        engine.manual_order(Side.BUY, quote_amount=100.0)
    Path(cfg.risk.kill_switch_file).unlink()


def test_manual_position_is_managed_by_the_bot(cfg):
    """A hand-opened position is still protected by its stop loss."""
    df = make_ohlcv([100.0, 100.0, 100.0, 94.0], spread=0.0)
    ts = df["ts"].tolist()
    clock = {"now": ts[0] + TF_MS}
    engine, exchange = build(cfg, StateStore(":memory:"), ScriptedStrategy(), lambda: clock["now"])
    exchange.set_price(100.0)
    engine.manual_order(Side.BUY, quote_amount=1000.0, stop_loss=96.0)
    clock["now"] = ts[3] + TF_MS
    exchange.set_price(94.0)
    engine.process_candle(df, fill_price=94.0, exit_at_level=True)
    assert engine.position is None
    assert engine.store.trades()[0].exit_reason == "stop_loss"


def exits_cfg(cfg, **kw):
    return cfg.model_copy(update={"exits": cfg.exits.model_copy(update=kw)})


def open_position(cfg, closes, *, stop_pct=0.05, tp_pct=0.5):
    """Helper: a scripted BUY on candle 4, then the caller drives the rest."""
    df = make_ohlcv(closes, spread=0.0)
    ts = df["ts"].tolist()
    strategy = ScriptedStrategy({ts[4]: buy_signal(closes[4], stop_pct=stop_pct, tp_pct=tp_pct)})
    clock = {"now": ts[0]}
    engine, exchange = build(cfg, StateStore(":memory:"), strategy, lambda: clock["now"])
    for i in range(5):
        clock["now"] = ts[i] + TF_MS
        exchange.set_price(float(df["close"].iloc[i]))
        engine.process_candle(df.iloc[: i + 1], fill_price=float(df["close"].iloc[i]), exit_at_level=True)
    assert engine.position is not None
    return engine, exchange, df, ts, clock


def test_trailing_stop_follows_the_high_and_never_drops(cfg):
    # atr_stop_mult is 2.0 by default, so a 5% stop implies an ATR of 2.5% of price.
    engine, exchange, df, ts, clock = open_position(exits_cfg(cfg, trailing_atr_mult=1.0), [100.0] * 8)
    pos = engine.position
    assert pos.atr_at_entry == pytest.approx(2.5)
    assert pos.stop_loss == pytest.approx(95.0)

    clock["now"] = ts[5] + TF_MS
    engine.manage_position(high=110.0, low=99.0, candle_ts=ts[5], now=clock["now"], market_price=109.0)
    assert engine.position.stop_loss == pytest.approx(107.5)  # 110 - 1 ATR
    assert engine.position.highest_price == 110.0

    # a lower high must not lower the stop
    engine.manage_position(high=108.0, low=108.0, candle_ts=ts[6], now=clock["now"], market_price=108.0)
    assert engine.position.stop_loss == pytest.approx(107.5)

    # and the trailing stop closes the trade when price comes back to it
    engine.manage_position(high=108.0, low=107.0, candle_ts=ts[7], now=clock["now"], market_price=107.0)
    assert engine.position is None
    trade = engine.store.trades()[-1]
    assert trade.exit_reason == "stop_loss" and trade.pnl > 0, "the trailing stop banked a profit"


def test_breakeven_moves_the_stop_to_entry_plus_fees(cfg):
    engine, exchange, df, ts, clock = open_position(exits_cfg(cfg, breakeven_after_atr=1.0), [100.0] * 8)
    entry = engine.position.entry_price
    clock["now"] = ts[5] + TF_MS
    engine.manage_position(high=entry + 2.0, low=entry, candle_ts=ts[5], now=clock["now"], market_price=entry + 2.0)
    assert engine.position.stop_loss == pytest.approx(95.0), "not yet 1 ATR in profit"
    engine.manage_position(high=entry + 3.0, low=entry, candle_ts=ts[6], now=clock["now"], market_price=entry + 3.0)
    assert engine.position.stop_loss > entry, "the stop covers the entry and both fees"
    assert engine.position.stop_loss == pytest.approx(entry * (1 + cfg.exchange.fee_rate * 2))


def test_partial_take_profit_sells_a_fraction_once(cfg):
    engine, exchange, df, ts, clock = open_position(
        exits_cfg(cfg, partial_take_fraction=0.5, partial_take_atr=1.0), [100.0] * 8)
    full = engine.position.qty
    clock["now"] = ts[5] + TF_MS
    out = engine.manage_position(high=103.0, low=100.0, candle_ts=ts[5], now=clock["now"], market_price=103.0)
    assert out and "partial" in out
    assert engine.position.qty == pytest.approx(full / 2, rel=1e-3)
    assert engine.position.partial_done is True
    banked = engine.store.trades()[-1]
    assert banked.qty == pytest.approx(full / 2, rel=1e-3) and banked.pnl > 0

    # it does not fire again on the next candle
    engine.manage_position(high=104.0, low=100.0, candle_ts=ts[6], now=clock["now"], market_price=104.0)
    assert engine.position.qty == pytest.approx(full / 2, rel=1e-3)
    assert len(engine.store.trades()) == 1


def test_stop_is_checked_before_the_high_can_raise_it(cfg):
    """A candle that hits both the stop and a new high must exit at the stop."""
    engine, exchange, df, ts, clock = open_position(exits_cfg(cfg, trailing_atr_mult=0.5), [100.0] * 8)
    clock["now"] = ts[5] + TF_MS
    engine.manage_position(high=120.0, low=94.0, candle_ts=ts[5], now=clock["now"], open_price=100.0)
    assert engine.position is None
    assert engine.store.trades()[-1].exit_reason == "stop_loss"


def test_trend_filter_blocks_buys_below_the_trend_line(cfg):
    from bot.strategy.ema_rsi import EmaRsiStrategy

    falling = [100.0 * (0.995 ** i) for i in range(500)] + [100.0 * (0.995 ** 499) * (1.02 ** i) for i in range(60)]
    df = make_ohlcv(falling, spread=0.0)
    loose = EmaRsiStrategy({"rsi_buy_min": 0, "rsi_buy_max": 100})
    filtered = EmaRsiStrategy({"rsi_buy_min": 0, "rsi_buy_max": 100, "trend_filter_period": 200})
    buys_loose = [i for i in range(filtered.warmup, len(df))
                  if loose.on_candle(df.iloc[: i + 1]).action == Action.BUY]
    assert buys_loose, "the unfiltered strategy buys somewhere in this series"
    blocked = 0
    for i in buys_loose:
        sig = filtered.on_candle(df.iloc[: i + 1])
        if sig.action != Action.BUY:
            blocked += 1
            assert "trend line" in sig.reason or "not ready" in sig.reason
    assert blocked, "the trend filter blocks at least one buy while price is under the trend line"


def test_time_stop_closes_a_trade_that_went_nowhere(cfg, tmp_path):
    """A position that neither wins nor loses is capital doing nothing, and on real data
    the slow bleeders are where the money goes."""
    closes = [100.0] * 5 + [100.2] * 20  # bought, then flat for twenty candles
    df = make_ohlcv(closes, spread=0.0)
    ts = df["ts"].tolist()
    strategy = ScriptedStrategy({ts[4]: buy_signal(100.0, stop_pct=0.10, tp_pct=0.20)})
    clock = {"now": ts[0]}
    timed = cfg.model_copy(update={"exits": cfg.exits.model_copy(
        update={"time_stop_candles": 8, "time_stop_min_atr": 0.5})})
    engine, exchange = build(timed, StateStore(tmp_path / "s.sqlite"), strategy, lambda: clock["now"])

    out = None
    for i in range(len(df) - 1):
        clock["now"] = ts[i] + TF_MS
        price = float(df["open"].iloc[i + 1])
        exchange.set_price(price)
        out = engine.process_candle(df.iloc[: i + 1], fill_price=price, exit_at_level=True)
        if engine.position is None and i > 4:
            break
    assert engine.position is None, "the time stop must close it"
    assert out and out.get("time_stop"), out
    assert out["time_stop"]["candles"] >= 8
    trades = engine.store.trades()
    assert trades and trades[-1].exit_reason == "time_stop", "and it is recorded as its own exit"


def test_the_time_stop_leaves_a_working_trade_alone(cfg, tmp_path):
    closes = [100.0] * 5 + [100.0 + i for i in range(1, 21)]  # steadily in profit
    df = make_ohlcv(closes, spread=0.0)
    ts = df["ts"].tolist()
    strategy = ScriptedStrategy({ts[4]: buy_signal(100.0, stop_pct=0.10, tp_pct=0.50)})
    clock = {"now": ts[0]}
    timed = cfg.model_copy(update={"exits": cfg.exits.model_copy(
        update={"time_stop_candles": 8, "time_stop_min_atr": 0.5})})
    engine, exchange = build(timed, StateStore(tmp_path / "s.sqlite"), strategy, lambda: clock["now"])

    for i in range(len(df) - 1):
        clock["now"] = ts[i] + TF_MS
        price = float(df["open"].iloc[i + 1])
        exchange.set_price(price)
        engine.process_candle(df.iloc[: i + 1], fill_price=price, exit_at_level=True)
    assert engine.position is not None, "a trade that is working must be left to work"


def test_entry_filters_block_a_buy_but_never_an_exit(cfg, tmp_path):
    # Flat prices, so nothing but the scripted signals ever closes the position.
    df = make_ohlcv([100.0] * 9, spread=0.0)
    ts = df["ts"].tolist()
    strategy = ScriptedStrategy({ts[4]: buy_signal(100.0), ts[6]: Signal(Action.SELL, 1.0, "get out")})
    clock = {"now": ts[0]}
    # A window that excludes every hour in the fixture: the entry is refused, not the exit.
    hour = pd.Timestamp(ts[4], unit="ms", tz="UTC").hour
    window = f"{(hour + 2) % 24}-{(hour + 3) % 24}"
    filtered = cfg.model_copy(update={"filters": cfg.filters.model_copy(update={"hours_utc": window})})
    engine, exchange = build(filtered, StateStore(tmp_path / "s.sqlite"), strategy, lambda: clock["now"])

    for i in range(5):
        clock["now"] = ts[i] + TF_MS
        price = float(df["open"].iloc[i + 1])
        exchange.set_price(price)
        out = engine.process_candle(df.iloc[: i + 1], fill_price=price, exit_at_level=True)
    assert engine.position is None, "the filter blocked the entry"
    assert "outside the trading window" in out["filtered"]
    assert out["signal"]["action"] == "HOLD" and "entry filtered" in out["signal"]["reason"]

    # Open a position with the filter off, then switch it on: the exit must still fire, or
    # a filter could trap a trade in a market it has decided not to trade.
    second = ScriptedStrategy({ts[4]: buy_signal(100.0), ts[6]: Signal(Action.SELL, 1.0, "get out")})
    open_engine, open_exchange = build(cfg, StateStore(tmp_path / "s2.sqlite"), second, lambda: clock["now"])
    for i in range(5):
        clock["now"] = ts[i] + TF_MS
        price = float(df["open"].iloc[i + 1])
        open_exchange.set_price(price)
        open_engine.process_candle(df.iloc[: i + 1], fill_price=price, exit_at_level=True)
    assert open_engine.position is not None, "the entry went through with no filter"

    open_engine.filters = engine.filters  # the same blocking filter, now with a trade open
    for i in range(5, 7):
        clock["now"] = ts[i] + TF_MS
        price = float(df["open"].iloc[i + 1])
        open_exchange.set_price(price)
        out = open_engine.process_candle(df.iloc[: i + 1], fill_price=price, exit_at_level=True)
    assert open_engine.position is None, "an exit is never filtered"
    assert out["order"]["side"] == "sell" and "filtered" not in out
