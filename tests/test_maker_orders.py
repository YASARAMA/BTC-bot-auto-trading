"""Maker (limit) orders: what they save in fees, and what they cost in missed trades."""
from __future__ import annotations

import pytest

from bot.config import load_config
from bot.data.history import load_csv
from bot.execution.base import ExchangeClient, MarketInfo
from bot.execution.executor import OrderExecutor
from bot.execution.order_id import is_bot_order_id, make_client_order_id
from bot.execution.paper import PaperExchange
from bot.models import Order, OrderIntent, Side
from bot.state.store import StateStore

SYMBOL = "BTC/USDT"
TS = 1_700_000_000_000


def paper(cash=10_000.0, base=0.0, maker=0.0002, **kw) -> PaperExchange:
    ex = PaperExchange(symbol=SYMBOL, fee_rate=0.001, slippage_bps=10.0, maker_fee_rate=maker,
                       initial_cash=cash, initial_base=base, clock=lambda: TS,
                       market_info=MarketInfo(amount_step=1e-5, price_step=0.01, min_amount=1e-4,
                                              min_notional=10.0), **kw)
    ex.set_price(50_000.0)
    return ex


def executor(exchange, **kw) -> tuple[OrderExecutor, StateStore]:
    store = StateStore(":memory:")
    ex = OrderExecutor(exchange, store, symbol=SYMBOL, strategy_name="ema_rsi", order_timeout_seconds=0.0,
                       order_poll_seconds=0.0, sleep=lambda _s: None, clock=lambda: TS,
                       wait_for_fill=False, **kw)
    return ex, store


def intent(side=Side.BUY, qty=0.1, kind="entry", price=50_000.0) -> OrderIntent:
    return OrderIntent(side=side, qty=qty, ref_price=price, kind=kind, candle_ts=TS, reason="t",
                       stop_loss=49_000.0, take_profit=52_000.0)


# ---------------------------------------------------------------- the paper exchange


def test_a_resting_buy_fills_only_when_the_price_comes_down_to_it():
    ex = paper()
    ex.set_fill_window(50_100.0, 49_900.0)  # the candle traded down through 49,950
    o = ex.create_limit_order(SYMBOL, Side.BUY, 0.1, 49_950.0, "cid-fill")
    assert o.status == "closed" and o.filled == pytest.approx(0.1)
    assert o.avg_price == pytest.approx(49_950.0), "a resting order fills at its own price, not worse"
    assert o.fee == pytest.approx(0.1 * 49_950.0 * 0.0002), "maker fee, not taker"
    assert ex.fetch_balance()["BTC"]["total"] == pytest.approx(0.1)

    miss = paper()
    miss.set_fill_window(50_400.0, 50_100.0)  # never traded that low
    o2 = miss.create_limit_order(SYMBOL, Side.BUY, 0.1, 49_950.0, "cid-miss")
    assert o2.status == "open" and o2.filled == 0.0
    assert miss.fetch_balance()["USDT"]["free"] == pytest.approx(10_000.0), "no money moves on a miss"


def test_a_resting_sell_fills_only_when_the_price_comes_up_to_it():
    ex = paper(cash=0.0, base=0.1)
    ex.set_fill_window(50_200.0, 49_800.0)
    o = ex.create_limit_order(SYMBOL, Side.SELL, 0.1, 50_050.0, "cid-sell")
    assert o.status == "closed" and o.avg_price == pytest.approx(50_050.0)
    assert ex.fetch_balance()["USDT"]["free"] == pytest.approx(0.1 * 50_050.0 * (1 - 0.0002))

    miss = paper(cash=0.0, base=0.1)
    miss.set_fill_window(49_900.0, 49_500.0)
    assert miss.create_limit_order(SYMBOL, Side.SELL, 0.1, 50_050.0, "cid-sell2").status == "open"


def test_a_limit_order_pays_no_slippage():
    taker = paper()
    maker = paper()
    maker.set_fill_window(50_100.0, 49_000.0)
    market = taker.create_market_order(SYMBOL, Side.BUY, 0.1, "m")
    limit = maker.create_limit_order(SYMBOL, Side.BUY, 0.1, 50_000.0, "l")
    assert market.avg_price == pytest.approx(50_050.0)   # 10 bps of slippage
    assert limit.avg_price == pytest.approx(50_000.0)    # none
    assert limit.fee < market.fee


def test_a_resting_order_fills_later_when_the_price_reaches_it():
    ex = paper()
    ex.set_fill_window(50_500.0, 50_200.0)
    o = ex.create_limit_order(SYMBOL, Side.BUY, 0.1, 49_950.0, "cid-later")
    assert o.status == "open"
    ex.set_price(49_940.0)  # the market comes to us
    assert ex.fetch_order(o).status == "closed"
    assert ex.fetch_order(o).avg_price == pytest.approx(49_950.0)


def test_limit_orders_respect_balances_minimums_and_duplicate_ids():
    poor = paper(cash=100.0)
    poor.set_fill_window(50_000.0, 49_000.0)
    rejected = poor.create_limit_order(SYMBOL, Side.BUY, 1.0, 49_500.0, "cid-poor")
    assert rejected.status == "rejected" and "insufficient USDT" in rejected.reason

    tiny = paper()
    tiny.set_fill_window(50_000.0, 49_000.0)
    small = tiny.create_limit_order(SYMBOL, Side.BUY, 0.0000001, 49_500.0, "cid-small")
    assert small.status == "rejected" and "minimums" in small.reason

    ex = paper()
    ex.set_fill_window(50_000.0, 49_000.0)
    first = ex.create_limit_order(SYMBOL, Side.BUY, 0.1, 49_500.0, "cid-dup")
    again = ex.create_limit_order(SYMBOL, Side.BUY, 0.1, 49_500.0, "cid-dup")
    assert again is first or again.client_order_id == first.client_order_id
    assert ex.fetch_balance()["BTC"]["total"] == pytest.approx(0.1), "the duplicate must not buy twice"


def test_clearing_the_fill_window_stops_speculative_fills():
    ex = paper()
    ex.set_fill_window(50_000.0, 49_000.0)
    ex.set_fill_window(None, None)
    o = ex.create_limit_order(SYMBOL, Side.BUY, 0.1, 49_500.0, "cid-nowindow")
    assert o.status == "open", "with no range to match against, nothing may fill"


# ---------------------------------------------------------------- the executor


def test_the_executor_posts_below_the_price_to_buy_and_above_it_to_sell():
    ex, _ = executor(paper(), order_type="limit", limit_offset_bps=20.0)
    assert ex.limit_price(intent(Side.BUY), 0.01) == pytest.approx(49_900.0)
    assert ex.limit_price(intent(Side.SELL), 0.01) == pytest.approx(50_100.0)
    # Rounding always goes away from the market, so it can never turn into a taker order.
    coarse = ex.limit_price(intent(Side.BUY, price=50_003.0), 10.0)
    assert coarse <= 50_003.0 * (1 - 0.002) and coarse % 10 == 0
    coarse_sell = ex.limit_price(intent(Side.SELL, price=50_003.0), 10.0)
    assert coarse_sell >= 50_003.0 * (1 + 0.002) and coarse_sell % 10 == 0


def test_entries_are_posted_but_exits_always_cross_the_spread():
    exchange = paper(base=0.2)
    ex, store = executor(exchange, order_type="limit", limit_offset_bps=20.0)
    exchange.set_fill_window(50_100.0, 49_800.0)
    entry = ex.execute(intent(Side.BUY))
    assert entry is not None and entry.type == "limit" and entry.price == pytest.approx(49_900.0)

    exit_order = ex.execute(intent(Side.SELL, kind="exit"))
    assert exit_order is not None and exit_order.type == "market", "an exit must not wait for a price"
    assert exit_order.avg_price == pytest.approx(50_000.0 * (1 - 0.001))


def test_an_unfilled_maker_order_is_a_missed_trade_unless_a_fallback_is_asked_for():
    exchange = paper()
    exchange.set_fill_window(50_400.0, 50_100.0)  # never reaches a buy posted below the price
    ex, store = executor(exchange, order_type="limit", limit_offset_bps=20.0)
    assert ex.execute(intent()) is None, "no fill means no trade"
    assert exchange.fetch_balance()["BTC"]["total"] == 0.0
    stored = store.get_order(make_client_order_id("ema_rsi", SYMBOL, TS, "buy"))
    assert stored is not None and stored.status == "canceled" and stored.type == "limit"

    exchange2 = paper()
    exchange2.set_fill_window(50_400.0, 50_100.0)
    ex2, store2 = executor(exchange2, order_type="limit", limit_offset_bps=20.0, limit_fallback_market=True)
    filled = ex2.execute(intent())
    assert filled is not None and filled.type == "market"
    # The fallback needs its own id, and it still has to look like one of ours.
    fallback_cid = make_client_order_id("ema_rsi", SYMBOL, TS, "buy", attempt=1)
    assert filled.client_order_id == fallback_cid and is_bot_order_id(fallback_cid)
    assert fallback_cid != make_client_order_id("ema_rsi", SYMBOL, TS, "buy")
    assert store2.get_order(fallback_cid).filled == pytest.approx(0.1)


def test_attempt_zero_keeps_the_id_earlier_versions_wrote():
    assert make_client_order_id("s", SYMBOL, TS, "buy", attempt=0) == make_client_order_id("s", SYMBOL, TS, "buy")


def test_market_orders_are_untouched_by_the_limit_settings():
    exchange = paper()
    ex, _ = executor(exchange, order_type="market", limit_offset_bps=50.0)
    order = ex.execute(intent())
    assert order is not None and order.type == "market" and order.price is None


def test_an_exchange_that_cannot_post_orders_uses_market_orders_instead(caplog):
    """Not every client supports limit orders. Saying so once and trading anyway beats
    crashing, and beats silently doing nothing."""

    class MarketOnly(PaperExchange):
        # Hides the inherited implementation the way a client without the feature would.
        create_limit_order = ExchangeClient.create_limit_order

    exchange = MarketOnly(symbol=SYMBOL, fee_rate=0.001, slippage_bps=10.0, initial_cash=10_000.0,
                          clock=lambda: TS)
    exchange.set_price(50_000.0)
    assert exchange.supports_limit_orders is False
    assert paper().supports_limit_orders is True

    ex, _ = executor(exchange, order_type="limit", limit_offset_bps=20.0)
    assert ex.wants_limit(intent()) is False
    order = ex.execute(intent())
    assert order is not None and order.type == "market"


# ---------------------------------------------------------------- storage and backtest


def test_the_limit_price_survives_a_restart():
    store = StateStore(":memory:")
    order = Order(client_order_id="c", symbol=SYMBOL, side=Side.BUY, type="limit", price=49_900.0,
                  amount=0.1, status="open", created_at=TS, updated_at=TS)
    store.save_order(order)
    assert store.get_order("c").price == pytest.approx(49_900.0)


def test_a_database_from_an_older_build_gains_the_price_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE orders (client_order_id TEXT PRIMARY KEY, exchange_order_id TEXT,
        symbol TEXT NOT NULL, side TEXT NOT NULL, type TEXT NOT NULL, amount REAL NOT NULL,
        filled REAL NOT NULL DEFAULT 0, avg_price REAL, fee REAL NOT NULL DEFAULT 0, status TEXT NOT NULL,
        candle_ts INTEGER, kind TEXT, reason TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
    conn.execute("INSERT INTO orders VALUES ('old', 'x', 'BTC/USDT', 'buy', 'market', 0.1, 0.1, 100.0, 0.1,"
                 " 'closed', 1, 'entry', 'r', 1, 1)")
    conn.commit()
    conn.close()

    store = StateStore(path)
    assert store.get_order("old").price is None, "the old row simply has no limit price"
    store.save_order(Order(client_order_id="new", symbol=SYMBOL, side=Side.BUY, type="limit", price=42.0,
                           amount=0.1, status="open", created_at=1, updated_at=1))
    assert store.get_order("new").price == pytest.approx(42.0)


def test_maker_orders_trade_less_and_pay_less_in_a_real_backtest():
    from bot.backtest.engine import run_backtest

    cfg = load_config("config.yaml")
    df = load_csv("data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv").iloc[:2500].reset_index(drop=True)

    def run(**exchange_kw):
        return run_backtest(df, cfg.model_copy(update={"exchange": cfg.exchange.model_copy(update=exchange_kw)}))

    market = run(order_type="market")
    maker = run(order_type="limit", limit_offset_bps=20.0, maker_fee_rate=0.0002)
    assert market.metrics["trades"] > 0 and maker.metrics["trades"] > 0
    # Posting away from the price means some entries are never taken - that is the cost
    # of the cheaper fee, and a backtest that hid it would be lying.
    assert maker.metrics["trades"] <= market.metrics["trades"]
    assert maker.metrics["total_fees"] < market.metrics["total_fees"]
    assert all(t.entry_price > 0 and t.exit_price > 0 for t in maker.trades)

    with_fallback = run(order_type="limit", limit_offset_bps=20.0, maker_fee_rate=0.0002,
                        limit_fallback_market=True)
    assert with_fallback.metrics["trades"] >= maker.metrics["trades"], "the fallback takes the missed trades"
