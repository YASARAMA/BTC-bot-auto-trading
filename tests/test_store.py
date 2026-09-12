from bot.models import Order, Side, Trade
from bot.state.store import StateStore


def test_order_roundtrip_and_update(tmp_path):
    store = StateStore(tmp_path / "s.sqlite")
    o = Order(client_order_id="bot" + "a" * 24, symbol="BTC/USDT", side=Side.BUY, type="market", amount=0.1,
              status="open", created_at=1, updated_at=1, candle_ts=99, kind="entry", reason="r")
    store.save_order(o)
    assert store.open_orders()[0].client_order_id == o.client_order_id
    o.status, o.filled, o.avg_price, o.fee, o.exchange_order_id = "closed", 0.1, 100.0, 0.01, "x1"
    store.save_order(o)
    got = store.get_order(o.client_order_id)
    assert got.status == "closed" and got.filled == 0.1 and got.exchange_order_id == "x1" and got.kind == "entry"
    assert store.open_orders() == [] and len(store.recent_orders()) == 1
    assert store.get_order("missing") is None


def test_trades_equity_and_state(tmp_path):
    store = StateStore(tmp_path / "s.sqlite")
    t = Trade("BTC/USDT", "ema_rsi", 0.1, 1, 100.0, 2, 110.0, 0.2, 0.8, 0.8, "take_profit", "a", "b")
    store.save_trade(t)
    assert store.trades() == [t]
    store.save_equity(1, 100.0, 100.0, 0.0, 10.0)
    store.save_equity(1, 101.0, 101.0, 0.0, 10.0)  # same ts replaces
    store.save_equity(2, 102.0, 50.0, 0.5, 10.0)
    curve = store.equity_curve()
    assert [c["equity"] for c in curve] == [101.0, 102.0]
    store.set_state("position", None)
    store.set_many([("a", {"x": 1}), ("b", [1, 2])])
    assert store.get_state("position") is None and store.get_state("a") == {"x": 1} and store.get_state("b") == [1, 2]
    assert store.get_state("nope", "dflt") == "dflt"
    store.close()
    # survives reopen
    again = StateStore(tmp_path / "s.sqlite")
    assert again.get_state("a") == {"x": 1} and len(again.trades()) == 1
