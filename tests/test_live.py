import pytest

from bot.config import ExchangeConfig
from bot.execution.live import LiveExchange, _precision_to_step
from bot.models import Order, Side


class FakeCcxt:
    """Just enough of the ccxt unified API for parsing and call-shape tests. No network."""

    precisionMode = 4  # TICK_SIZE

    def __init__(self):
        self.orders = []
        self.fail = 0

    def load_markets(self):
        return {}

    def market(self, symbol):
        return {"precision": {"amount": 1e-5, "price": 0.01},
                "limits": {"amount": {"min": 1e-5}, "cost": {"min": 5.0}}}

    def amount_to_precision(self, symbol, amount):
        import math

        return f"{math.floor(amount * 1e5) / 1e5:.5f}"

    def fetch_ticker(self, symbol):
        if self.fail:
            self.fail -= 1
            import ccxt

            raise ccxt.NetworkError("down")
        return {"last": 50_000.0}

    def fetch_balance(self):
        return {"free": {"USDT": 100.0, "BTC": 0.5}, "total": {"USDT": 100.0, "BTC": 0.5}}

    def create_order(self, symbol, type_, side, amount, price, params):
        self.orders.append((symbol, type_, side, amount, price, params))
        return {"id": "42", "clientOrderId": params["clientOrderId"], "symbol": symbol, "side": side, "type": type_,
                "status": "closed", "amount": amount, "filled": amount, "average": 50_010.0, "timestamp": 1,
                "fee": {"cost": 0.00001, "currency": "BTC"}}

    def fetch_order(self, oid, symbol, params=None):
        return {"id": oid, "symbol": symbol, "side": "buy", "status": "canceled", "amount": 0.1, "filled": 0.04,
                "average": 50_000.0, "fees": [{"cost": 2.0, "currency": "USDT"}]}

    def cancel_order(self, oid, symbol):
        return {}

    def fetch_open_orders(self, symbol):
        return [{"id": "7", "clientOrderId": "web", "symbol": symbol, "side": "buy", "status": "open", "amount": 1}]


def make():
    cfg = ExchangeConfig(max_retries=3, backoff_base_seconds=0.001, backoff_max_seconds=0.001)
    return LiveExchange(cfg, client=FakeCcxt(), sleep=lambda _s: None)


def test_market_order_sends_client_id_and_converts_base_fee():
    ex = make()
    o = ex.create_market_order("BTC/USDT", Side.BUY, 0.123456789, "botabc", ref_price=50_000.0)
    sym, typ, side, amt, price, params = ex.client.orders[0]
    assert typ == "market" and side == "buy" and amt == pytest.approx(0.12345) and price == 50_000.0
    assert params == {"clientOrderId": "botabc"}
    assert o.status == "closed" and o.filled == pytest.approx(0.12345) and o.exchange_order_id == "42"
    assert o.fee == pytest.approx(0.00001 * 50_010.0)  # fee paid in BTC converted to quote


def test_fetch_order_maps_status_and_partial_fill():
    ex = make()
    o = Order(client_order_id="botabc", exchange_order_id="42", symbol="BTC/USDT", side=Side.BUY, type="market",
              amount=0.1, status="open", created_at=1, updated_at=1, candle_ts=5, kind="entry")
    got = ex.fetch_order(o)
    assert got.status == "canceled" and got.filled == 0.04 and got.fee == 2.0 and got.candle_ts == 5 and got.kind == "entry"


def test_market_info_balance_and_retry():
    ex = make()
    info = ex.market_info("BTC/USDT")
    assert info.amount_step == 1e-5 and info.min_notional == 5.0
    assert ex.fetch_balance()["BTC"] == {"free": 0.5, "total": 0.5}
    ex.client.fail = 2
    assert ex.fetch_ticker_price("BTC/USDT") == 50_000.0
    assert [o.client_order_id for o in ex.fetch_open_orders("BTC/USDT")] == ["web"]


def test_precision_to_step():
    assert _precision_to_step(1e-5, 4) == 1e-5
    assert _precision_to_step(5, 2) == pytest.approx(1e-5)
    assert _precision_to_step(None, 2) is None
