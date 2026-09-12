import pytest

from bot.config import RiskConfig
from bot.models import Action, OrderIntent, Position, Rejection, Signal, Trade
from bot.risk.manager import AccountSnapshot, RiskManager, RiskState

DAY = 86_400_000
NOW = 1_700_000_000_000
PRICE = 50_000.0


def rm(tmp_path, **over) -> RiskManager:
    base = dict(risk_per_trade_pct=1.0, max_position_pct=25.0, max_daily_loss_pct=3.0, max_consecutive_losses=3,
                cooldown_minutes=240.0, min_seconds_between_trades=3600.0, min_confidence=0.6,
                min_order_notional=10.0, kill_switch_file=str(tmp_path / "KILL"))
    base.update(over)
    return RiskManager(RiskConfig(**base), fee_rate=0.001)


def acct(equity=10_000.0, cash=None, price=PRICE, now=NOW, position=None) -> AccountSnapshot:
    return AccountSnapshot(equity=equity, cash=equity if cash is None else cash, price=price, now_ms=now, position=position)


def buy(stop=49_500.0, conf=1.0) -> Signal:
    return Signal(Action.BUY, conf, "test", stop_loss=stop, take_profit=PRICE + 1000)


def pos(qty=0.05, stop=49_000.0, tp=52_000.0) -> Position:
    return Position(qty=qty, entry_price=PRICE, entry_ts=NOW - DAY, stop_loss=stop, take_profit=tp,
                    entry_order_id="bot" + "0" * 24, strategy="t")


def trade(pnl: float) -> Trade:
    return Trade("BTC/USDT", "t", 0.01, NOW - 1, PRICE, NOW, PRICE + pnl / 0.01, 0.0, pnl, pnl / PRICE, "exit", "a", "b")


def test_position_size_capped_by_max_position(tmp_path):
    out = rm(tmp_path).evaluate(buy(stop=49_500.0), acct(), NOW)
    assert isinstance(out, OrderIntent)
    # risk 1% of 10k = 100 over a 500 stop distance -> 0.2 BTC = 10k notional, capped at 25% = 2.5k -> 0.05
    assert out.qty == pytest.approx(0.05)
    assert out.side.value == "buy" and out.kind == "entry"
    assert out.stop_loss == 49_500.0 and out.candle_ts == NOW


def test_position_size_by_fixed_fractional_risk(tmp_path):
    out = rm(tmp_path).evaluate(buy(stop=45_000.0), acct(), NOW)
    assert isinstance(out, OrderIntent)
    assert out.qty == pytest.approx(100.0 / 5_000.0)  # 0.02 BTC = 1k notional, below the 2.5k cap


def test_position_size_capped_by_cash(tmp_path):
    out = rm(tmp_path).evaluate(buy(stop=45_000.0), acct(equity=10_000.0, cash=500.0), NOW)
    assert isinstance(out, OrderIntent)
    assert out.qty == pytest.approx(500.0 / (PRICE * 1.001))


def test_too_small_rejected(tmp_path):
    out = rm(tmp_path).evaluate(buy(stop=45_000.0), acct(equity=50.0), NOW)
    assert isinstance(out, Rejection) and out.code == "too_small"


def test_hold_and_position_state_rules(tmp_path):
    r = rm(tmp_path)
    assert r.evaluate(Signal.hold("x"), acct(), NOW).code == "hold"
    assert r.evaluate(Signal(Action.SELL, 1.0, "s"), acct(), NOW).code == "no_position"
    assert r.evaluate(buy(), acct(position=pos()), NOW).code == "already_long"
    exit_intent = r.evaluate(Signal(Action.SELL, 1.0, "s"), acct(position=pos(qty=0.07)), NOW)
    assert isinstance(exit_intent, OrderIntent) and exit_intent.kind == "exit" and exit_intent.qty == 0.07


def test_confidence_and_stop_validation(tmp_path):
    r = rm(tmp_path)
    assert r.evaluate(buy(conf=0.5), acct(), NOW).code == "low_confidence"
    assert r.evaluate(buy(stop=None), acct(), NOW).code == "bad_stop"
    assert r.evaluate(buy(stop=PRICE), acct(), NOW).code == "bad_stop"


def test_kill_switch_blocks_everything(tmp_path):
    r = rm(tmp_path)
    (tmp_path / "KILL").write_text("stop")
    assert r.evaluate(buy(), acct(), NOW).code == "kill_switch"
    assert r.evaluate(Signal(Action.SELL, 1.0, "s"), acct(position=pos()), NOW).code == "kill_switch"
    blocked = r.protective_exit(pos(stop=49_000.0), high=49_500.0, low=48_000.0, candle_ts=NOW)
    assert isinstance(blocked, Rejection) and blocked.code == "kill_switch"
    (tmp_path / "KILL").unlink()
    assert isinstance(r.evaluate(buy(), acct(), NOW), OrderIntent)


def test_daily_loss_halts_entries_but_not_exits(tmp_path):
    r = rm(tmp_path)
    r.roll_day(NOW, 10_000.0)
    assert isinstance(r.evaluate(buy(), acct(equity=9_800.0), NOW + 1), OrderIntent)  # -2%: fine
    out = r.evaluate(buy(), acct(equity=9_650.0), NOW + 2)  # -3.5%: halted for the day
    assert out.code == "halted"
    assert r.state.events and "halt" in r.state.events[-1]
    assert r.evaluate(buy(), acct(equity=9_900.0), NOW + 3).code == "halted"  # recovery does not lift it
    exit_out = r.evaluate(Signal(Action.SELL, 1.0, "s"), acct(equity=9_650.0, position=pos()), NOW + 4)
    assert isinstance(exit_out, OrderIntent)
    next_day = (NOW // DAY + 1) * DAY + 1
    assert isinstance(r.evaluate(buy(), acct(equity=9_650.0, now=next_day), next_day), OrderIntent)
    assert r.state.halted_until == 0


def test_consecutive_losses_trigger_cooldown(tmp_path):
    r = rm(tmp_path, min_seconds_between_trades=0.0)
    r.roll_day(NOW, 10_000.0)
    for i in range(2):
        assert not r.on_trade_closed(trade(-10.0), 10_000.0, NOW + i)
    events = r.on_trade_closed(trade(-10.0), 10_000.0, NOW + 2)
    assert events and "cooldown" in events[0]
    assert r.evaluate(buy(), acct(), NOW + 3).code == "cooldown"
    later = NOW + 240 * 60_000 + 10
    assert isinstance(r.evaluate(buy(), acct(now=later), later), OrderIntent)
    r.on_trade_closed(trade(+5.0), 10_000.0, later)
    assert r.state.consecutive_losses == 0


def test_min_time_between_trades(tmp_path):
    r = rm(tmp_path)
    r.record_entry(NOW)
    assert r.evaluate(buy(), acct(now=NOW + 10_000), NOW).code == "min_interval"
    assert isinstance(r.evaluate(buy(), acct(now=NOW + 3_601_000), NOW), OrderIntent)


def test_protective_exit_levels(tmp_path):
    r = rm(tmp_path)
    p = pos(stop=49_000.0, tp=52_000.0)
    assert r.protective_exit(p, high=50_500.0, low=49_500.0, candle_ts=NOW) is None
    stop = r.protective_exit(p, high=50_500.0, low=48_900.0, candle_ts=NOW)
    assert isinstance(stop, OrderIntent) and stop.kind == "stop_loss" and stop.ref_price == 49_000.0 and stop.qty == p.qty
    tp = r.protective_exit(p, high=52_100.0, low=50_000.0, candle_ts=NOW)
    assert isinstance(tp, OrderIntent) and tp.kind == "take_profit" and tp.ref_price == 52_000.0
    both = r.protective_exit(p, high=52_100.0, low=48_000.0, candle_ts=NOW)
    assert both.kind == "stop_loss"  # conservative: stop wins when both are inside the range
    assert r.protective_exit(None, 1, 0, NOW) is None


def test_risk_state_roundtrip():
    st = RiskState(day="2024-01-01", day_start_equity=1.0, consecutive_losses=2, cooldown_until=5, halted_until=6,
                   halt_reason="x", last_entry_ts=7, trades_today=1, realized_pnl_today=-3.0)
    st.events.append("not persisted")
    d = st.to_dict()
    assert "events" not in d
    assert RiskState.from_dict(d) == RiskState(**{k: v for k, v in d.items()})
    assert RiskState.from_dict({"day": "d", "unknown_future_field": 1}).day == "d"
