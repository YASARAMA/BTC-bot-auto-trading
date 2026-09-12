import numpy as np
import pandas as pd
import pytest

from bot.models import Action
from bot.strategy import get_strategy
from bot.strategy.ema_rsi import EmaRsiStrategy
from bot.strategy.indicators import atr, ema, rsi
from tests.conftest import make_ohlcv, trending_series


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.linspace(100, 200, 60))
    down = pd.Series(np.linspace(200, 100, 60))
    assert rsi(up, 14).dropna().between(0, 100).all()
    assert rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    assert rsi(down, 14).iloc[-1] == pytest.approx(0.0)
    flat = pd.Series(np.full(60, 100.0))
    assert rsi(flat, 14).iloc[-1] == pytest.approx(50.0)


def test_ema_and_atr_basic():
    df = make_ohlcv(trending_series())
    fast, slow = ema(df["close"], 12), ema(df["close"], 26)
    assert len(fast) == len(df) and not fast.isna().any()
    a = atr(df, 14)
    assert (a.dropna() > 0).all()
    # in a steady uptrend the fast EMA sits above the slow EMA
    mid = 120 + 100
    assert fast.iloc[mid] > slow.iloc[mid]


def _signals(strategy, df):
    out = []
    for i in range(len(df)):
        out.append(strategy.on_candle(df.iloc[: i + 1]))
    return out


def test_warmup_holds():
    s = get_strategy("ema_rsi", {})
    df = make_ohlcv(trending_series())
    sig = s.on_candle(df.iloc[: s.warmup - 1])
    assert sig.action == Action.HOLD and "warming up" in sig.reason


def test_cross_up_then_cross_down_produces_buy_then_sell():
    s = EmaRsiStrategy({"rsi_buy_min": 0, "rsi_buy_max": 100})
    df = make_ohlcv(trending_series())
    sigs = _signals(s, df)
    buys = [i for i, x in enumerate(sigs) if x.action == Action.BUY]
    sells = [i for i, x in enumerate(sigs) if x.action == Action.SELL]
    assert buys, "expected an EMA cross up"
    assert sells, "expected an EMA cross down"
    first_buy = buys[0]
    assert first_buy >= s.warmup
    assert any(k > first_buy for k in sells)
    b = sigs[first_buy]
    close = df["close"].iloc[first_buy]
    assert b.stop_loss is not None and b.take_profit is not None
    assert b.stop_loss < close < b.take_profit
    assert 0.5 <= b.confidence <= 1.0
    assert "ema_fast=" in b.reason and "rsi=" in b.reason
    # a crossover is an event: the next candle is not another BUY
    assert sigs[first_buy + 1].action != Action.BUY


def test_rsi_filter_blocks_buy():
    strict = EmaRsiStrategy({"rsi_buy_min": 99.0, "rsi_buy_max": 100.0})
    loose = EmaRsiStrategy({"rsi_buy_min": 0.0, "rsi_buy_max": 100.0})
    df = make_ohlcv(trending_series())
    loose_sigs = _signals(loose, df)
    first_buy = next(i for i, x in enumerate(loose_sigs) if x.action == Action.BUY)
    blocked = strict.on_candle(df.iloc[: first_buy + 1])
    assert blocked.action == Action.HOLD and "RSI" in blocked.reason


def test_stop_and_tp_use_atr_multiples():
    s = EmaRsiStrategy({"rsi_buy_min": 0, "rsi_buy_max": 100, "atr_stop_mult": 1.5, "atr_tp_mult": 4.0})
    df = make_ohlcv(trending_series())
    sigs = _signals(s, df)
    i = next(k for k, x in enumerate(sigs) if x.action == Action.BUY)
    ind = s.indicators(df.iloc[: i + 1])
    close, a = df["close"].iloc[i], ind["atr"].iloc[-1]
    assert sigs[i].stop_loss == pytest.approx(close - 1.5 * a)
    assert sigs[i].take_profit == pytest.approx(close + 4.0 * a)


def test_params_validation():
    with pytest.raises(ValueError):
        EmaRsiStrategy({"ema_fast": 30, "ema_slow": 20})
    with pytest.raises(ValueError):
        EmaRsiStrategy({"unknown_param": 1})
    with pytest.raises(KeyError):
        get_strategy("does_not_exist")


def test_strategy_is_pure():
    s = get_strategy("ema_rsi", {"rsi_buy_min": 0, "rsi_buy_max": 100})
    df = make_ohlcv(trending_series())
    a = s.on_candle(df)
    b = s.on_candle(df)
    assert a == b
