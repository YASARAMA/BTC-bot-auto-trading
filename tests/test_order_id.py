import re

from bot.execution.order_id import is_bot_order_id, make_client_order_id


def test_same_inputs_same_id():
    a = make_client_order_id("ema_rsi", "BTC/USDT", 1700000000000, "buy")
    b = make_client_order_id("ema_rsi", "BTC/USDT", 1700000000000, "buy")
    assert a == b


def test_any_input_change_changes_id():
    base = make_client_order_id("ema_rsi", "BTC/USDT", 1700000000000, "buy")
    assert make_client_order_id("ema_rsi", "BTC/USDT", 1700003600000, "buy") != base
    assert make_client_order_id("ema_rsi", "BTC/USDT", 1700000000000, "sell") != base
    assert make_client_order_id("other", "BTC/USDT", 1700000000000, "buy") != base
    assert make_client_order_id("ema_rsi", "ETH/USDT", 1700000000000, "buy") != base


def test_id_is_exchange_safe():
    cid = make_client_order_id("ema_rsi", "BTC/USDT", 1700000000000, "BUY")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,32}", cid)
    assert cid == make_client_order_id("ema_rsi", "BTC/USDT", 1700000000000, "buy")  # side is case-insensitive
    assert is_bot_order_id(cid)
    assert not is_bot_order_id("web_123")
    assert not is_bot_order_id(None)
