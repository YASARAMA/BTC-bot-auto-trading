import pytest

from bot.execution.executor import OrderExecutor
from bot.execution.paper import PaperExchange
from bot.models import OrderIntent, Side
from bot.state.store import StateStore


class FlakyExchange(PaperExchange):
    """Raises on the first order submission, like a dropped connection mid-request."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.fail_next = True

    def create_market_order(self, *a, **k):
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("socket closed")
        return super().create_market_order(*a, **k)


def make_executor(exchange):
    store = StateStore(":memory:")
    ex = OrderExecutor(exchange, store, symbol="BTC/USDT", strategy_name="ema_rsi", order_timeout_seconds=0.0,
                       order_poll_seconds=0.0, sleep=lambda _s: None, clock=lambda: 1_700_000_000_000)
    return ex, store


def intent(ts=1_700_000_000_000, qty=0.1) -> OrderIntent:
    return OrderIntent(side=Side.BUY, qty=qty, ref_price=50_000.0, kind="entry", candle_ts=ts, reason="t",
                       stop_loss=49_000.0, take_profit=52_000.0)


def paper(**kw):
    ex = PaperExchange(symbol="BTC/USDT", fee_rate=0.001, slippage_bps=0.0, initial_cash=10_000.0, clock=lambda: 1, **kw)
    ex.set_price(50_000.0)
    return ex


def test_same_candle_never_places_twice():
    exchange = paper()
    ex, store = make_executor(exchange)
    first = ex.execute(intent())
    assert first is not None and first.filled == pytest.approx(0.1)
    cash = exchange.fetch_balance()["USDT"]["free"]
    second = ex.execute(intent())
    assert second is not None and second.client_order_id == first.client_order_id
    assert exchange.fetch_balance()["USDT"]["free"] == cash
    assert exchange.fetch_balance()["BTC"]["total"] == pytest.approx(0.1)
    assert len(store.recent_orders()) == 1


def test_order_is_persisted_before_submission_and_resolved_after_a_crash():
    exchange = FlakyExchange(symbol="BTC/USDT", fee_rate=0.001, slippage_bps=0.0, initial_cash=10_000.0, clock=lambda: 1)
    exchange.set_price(50_000.0)
    ex, store = make_executor(exchange)
    with pytest.raises(ConnectionError):
        ex.execute(intent())
    pending = store.open_orders()
    assert len(pending) == 1 and pending[0].status == "open"
    # On retry the executor resumes the pending order instead of creating a second one. The paper
    # exchange never saw it, so it times out, is cancelled locally and nothing is filled.
    assert ex.execute(intent()) is None
    assert store.open_orders() == []
    assert store.get_order(pending[0].client_order_id).status == "canceled"
    assert exchange.fetch_balance()["BTC"]["total"] == 0.0


def test_below_exchange_minimum_is_not_sent():
    exchange = paper()
    ex, store = make_executor(exchange)
    assert ex.execute(intent(qty=0.00001)) is None
    assert store.recent_orders() == []


def test_partial_fill_reported():
    exchange = paper(partial_fill_ratio=0.4)
    ex, _ = make_executor(exchange)
    order = ex.execute(intent())
    assert order is not None and order.filled == pytest.approx(0.04) and order.amount == pytest.approx(0.1)
