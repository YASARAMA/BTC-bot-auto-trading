import pytest

from bot.execution.base import MarketInfo
from bot.execution.paper import PaperExchange
from bot.models import Side


def make(cash=10_000.0, base=0.0, **kw) -> PaperExchange:
    ex = PaperExchange(symbol="BTC/USDT", fee_rate=0.001, slippage_bps=10.0, initial_cash=cash, initial_base=base,
                       market_info=MarketInfo(amount_step=1e-5, min_amount=1e-4, min_notional=10.0), clock=lambda: 1, **kw)
    ex.set_price(50_000.0)
    return ex


def test_buy_fill_applies_slippage_and_fee():
    ex = make()
    o = ex.create_market_order("BTC/USDT", Side.BUY, 0.1, "cid-1")
    assert o.status == "closed" and o.filled == pytest.approx(0.1)
    assert o.avg_price == pytest.approx(50_050.0)  # +10 bps
    assert o.fee == pytest.approx(5.005)
    bal = ex.fetch_balance()
    assert bal["BTC"]["total"] == pytest.approx(0.1)
    assert bal["USDT"]["free"] == pytest.approx(10_000.0 - 5_005.0 - 5.005)


def test_sell_fill_applies_slippage_and_fee():
    ex = make(cash=0.0, base=0.1)
    o = ex.create_market_order("BTC/USDT", Side.SELL, 0.1, "cid-2")
    assert o.avg_price == pytest.approx(49_950.0)
    assert ex.fetch_balance()["USDT"]["free"] == pytest.approx(4_995.0 - 4.995)
    assert ex.fetch_balance()["BTC"]["total"] == pytest.approx(0.0)


def test_insufficient_funds_rejected():
    ex = make(cash=100.0)
    o = ex.create_market_order("BTC/USDT", Side.BUY, 1.0, "cid-3")
    assert o.status == "rejected" and "insufficient USDT" in o.reason
    assert ex.fetch_balance()["USDT"]["free"] == 100.0
    o2 = ex.create_market_order("BTC/USDT", Side.SELL, 0.5, "cid-4")
    assert o2.status == "rejected" and "insufficient BTC" in o2.reason


def test_partial_fill():
    ex = make(partial_fill_ratio=0.5)
    o = ex.create_market_order("BTC/USDT", Side.BUY, 0.1, "cid-5")
    assert o.status == "closed" and o.filled == pytest.approx(0.05) and o.amount == pytest.approx(0.1)
    assert o.remaining == pytest.approx(0.05)
    assert ex.fetch_balance()["BTC"]["total"] == pytest.approx(0.05)


def test_duplicate_client_id_is_idempotent():
    ex = make()
    a = ex.create_market_order("BTC/USDT", Side.BUY, 0.1, "same")
    cash_after = ex.fetch_balance()["USDT"]["free"]
    b = ex.create_market_order("BTC/USDT", Side.BUY, 0.1, "same")
    assert a is b
    assert ex.fetch_balance()["USDT"]["free"] == cash_after
    assert ex.fetch_balance()["BTC"]["total"] == pytest.approx(0.1)


def test_quantity_rounded_down_to_step_and_minimums():
    ex = make()
    o = ex.create_market_order("BTC/USDT", Side.BUY, 0.123456789, "cid-6")
    assert o.amount == pytest.approx(0.12345)
    tiny = ex.create_market_order("BTC/USDT", Side.BUY, 0.00001, "cid-7")
    assert tiny.status == "rejected"
    assert ex.fetch_open_orders("BTC/USDT") == []
