"""Following the entries the filters refused, so "it felt safer" can become a number."""
from __future__ import annotations

import pytest

from bot.reports import refused_entries
from bot.state.store import StateStore
from tests.conftest import T0, TF_MS


def shadow(store, ts, price=100.0, stop=98.0, take=104.0, reason="weekend (Sat)"):
    return store.save_shadow(ts=ts, symbol="BTC/USDT", reason=reason, price=price,
                             stop_loss=stop, take_profit=take, atr=1.0)


def test_the_store_keeps_refused_entries_until_they_resolve():
    store = StateStore(":memory:")
    first = shadow(store, T0)
    shadow(store, T0 + TF_MS)
    assert len(store.open_shadows("BTC/USDT")) == 2
    assert store.open_shadows("ETH/USDT") == [], "each market keeps its own"

    store.close_shadow(first, outcome="win", exit_ts=T0 + 5 * TF_MS, exit_price=104.0, pnl_pct=4.0)
    assert len(store.open_shadows("BTC/USDT")) == 1
    rows = store.shadows()
    assert len(rows) == 2 and {r["status"] for r in rows} == {"open", "closed"}
    store.close()


def test_a_refused_entry_that_hits_its_stop_counts_as_a_loss(cfg, tmp_path):
    from bot.engine import TradingEngine
    from bot.execution.executor import OrderExecutor
    from bot.execution.paper import PaperExchange
    from bot.notify.notifier import Notifier
    from bot.risk.manager import RiskManager
    from tests.conftest import ScriptedStrategy

    store = StateStore(tmp_path / "s.sqlite")
    exchange = PaperExchange(symbol="BTC/USDT", fee_rate=0.001, slippage_bps=0.0, initial_cash=1000.0,
                             clock=lambda: T0)
    engine = TradingEngine(
        cfg=cfg, strategy=ScriptedStrategy(), risk=RiskManager(cfg.risk, fee_rate=0.001), exchange=exchange,
        executor=OrderExecutor(exchange, store, symbol="BTC/USDT", strategy_name="s",
                               order_timeout_seconds=0.0, order_poll_seconds=0.0, sleep=lambda _s: None,
                               clock=lambda: T0),
        store=store, notifier=Notifier(cfg.notify), clock=lambda: T0, mode="test")

    shadow(store, T0, price=100.0, stop=98.0, take=104.0)
    # A candle that trades down to 97: the stop was reached.
    engine.follow_shadows(high=101.0, low=97.0, candle_ts=T0 + TF_MS, price=99.0)
    rows = store.shadows()
    assert rows[0]["status"] == "closed" and rows[0]["outcome"] == "loss"
    assert rows[0]["pnl_pct"] == pytest.approx(-2.0)

    # A candle that touches both counts as the loss: within one candle the order is unknowable.
    shadow(store, T0 + 2 * TF_MS, price=100.0, stop=98.0, take=104.0)
    engine.follow_shadows(high=105.0, low=97.0, candle_ts=T0 + 3 * TF_MS, price=104.0)
    assert [r for r in store.shadows() if r["ts"] == T0 + 2 * TF_MS][0]["outcome"] == "loss"


def test_a_refused_entry_that_goes_nowhere_times_out(cfg, tmp_path):
    from bot.engine import TradingEngine
    from bot.execution.executor import OrderExecutor
    from bot.execution.paper import PaperExchange
    from bot.notify.notifier import Notifier
    from bot.risk.manager import RiskManager
    from tests.conftest import ScriptedStrategy

    tuned = cfg.model_copy(update={"filters": cfg.filters.model_copy(update={"refused_entry_candles": 3})})
    store = StateStore(tmp_path / "s.sqlite")
    exchange = PaperExchange(symbol="BTC/USDT", fee_rate=0.001, slippage_bps=0.0, initial_cash=1000.0,
                             clock=lambda: T0)
    engine = TradingEngine(
        cfg=tuned, strategy=ScriptedStrategy(), risk=RiskManager(tuned.risk, fee_rate=0.001),
        exchange=exchange,
        executor=OrderExecutor(exchange, store, symbol="BTC/USDT", strategy_name="s",
                               order_timeout_seconds=0.0, order_poll_seconds=0.0, sleep=lambda _s: None,
                               clock=lambda: T0),
        store=store, notifier=Notifier(tuned.notify), clock=lambda: T0, mode="test")

    shadow(store, T0, price=100.0, stop=90.0, take=120.0)
    engine.follow_shadows(high=101.0, low=99.0, candle_ts=T0 + TF_MS, price=100.5)
    assert store.shadows()[0]["status"] == "open", "still inside the horizon"
    engine.follow_shadows(high=101.0, low=99.0, candle_ts=T0 + 3 * TF_MS, price=100.5)
    assert store.shadows()[0]["outcome"] == "timeout"


def test_the_verdict_says_which_filter_costs_the_most():
    rows = [
        {"status": "closed", "pnl_pct": -2.0, "reason": "weekend (Sat)"},
        {"status": "closed", "pnl_pct": -3.0, "reason": "weekend (Sun)"},
        {"status": "closed", "pnl_pct": 5.0, "reason": "24x timeframe trend is falling"},
        {"status": "open", "pnl_pct": None, "reason": "weekend (Sat)"},
    ]
    out = refused_entries(rows)
    assert out["refused"] == 4 and out["resolved"] == 3 and out["still_open"] == 1
    assert out["would_have_won"] == 1 and out["would_have_lost"] == 2
    assert out["net_pnl_pct"] == pytest.approx(0.0)
    assert "earning their place" in out["verdict"]
    # The costliest filter sorts first, so the fix is the top line.
    assert list(out["by_reason"])[0] == "weekend (Sun)"


def test_the_verdict_calls_out_filters_that_only_remove_winners():
    rows = [{"status": "closed", "pnl_pct": 4.0, "reason": "hour 23:00 UTC is outside"} for _ in range(5)]
    out = refused_entries(rows)
    assert "costing you money" in out["verdict"] and out["net_pnl_pct"] == pytest.approx(20.0)


def test_no_refusals_is_said_plainly():
    assert "not refused an entry yet" in refused_entries([])["verdict"]
    assert "Give the filters a few weeks" in refused_entries([{"status": "open", "pnl_pct": None}])["verdict"]
