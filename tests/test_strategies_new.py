"""Breakout, mean reversion, the regime switch between them, and the buy-and-hold benchmark."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from bot.models import Action
from bot.strategy import STRATEGIES, get_strategy
from bot.strategy.breakout import BreakoutStrategy
from bot.strategy.buy_hold import BuyHoldStrategy
from bot.strategy.indicators import adx, bollinger, donchian
from bot.strategy.mean_reversion import MeanReversionStrategy
from bot.strategy.regime import RegimeStrategy
from tests.conftest import make_ohlcv, trending_series


def ranging_series(n: int = 400, mid: float = 100.0, amp: float = 4.0, period: int = 40):
    """A sideways market: a clean sine wave around a flat average."""
    return [mid + amp * math.sin(2 * math.pi * i / period) for i in range(n)]


def dips_in_uptrend(n: int = 400, start: float = 100.0, drift: float = 0.004,
                    shock: float = 0.90, every: int = 25):
    """A market that grinds upwards and drops hard every so often.

    A smooth wave never breaks its own Bollinger band, because the band widens along with
    it; the pullbacks have to be sharp before mean reversion has anything to buy.
    """
    out, price = [], start
    for i in range(n):
        price = price * shock if i and i % every == 0 else price * (1 + drift)
        out.append(price)
    return out


def falling_with_dips(n: int = 400, start: float = 100.0, drift: float = -0.004,
                      shock: float = 0.90, every: int = 25):
    """The same shape pointing down: the trend filter must refuse to buy these dips."""
    return dips_in_uptrend(n, start, drift, shock, every)


def signals(strategy, df, start: int | None = None):
    """Replay the frame candle by candle, exactly as the engine does."""
    first = strategy.warmup if start is None else start
    return [strategy.on_candle(df.iloc[: i + 1]) for i in range(first, len(df))]


def actions(sigs):
    return [s.action for s in sigs]


# ---------------------------------------------------------------- indicators


def test_donchian_excludes_the_current_candle():
    df = make_ohlcv([10, 11, 12, 13, 20, 14, 15])
    high, low = donchian(df, 3)
    # The channel at the spike candle (index 4) knows only candles 1..3.
    assert high.iloc[4] == pytest.approx(df["high"].iloc[1:4].max())
    assert high.iloc[4] < df["high"].iloc[4], "the breakout candle must not define its own level"
    assert low.iloc[4] == pytest.approx(df["low"].iloc[1:4].min())
    assert high.iloc[:3].isna().all() and low.iloc[:3].isna().all()


def test_donchian_shift_zero_includes_current_candle():
    df = make_ohlcv([10, 11, 12, 13, 20])
    high, _ = donchian(df, 3, shift=0)
    assert high.iloc[4] == pytest.approx(df["high"].iloc[4])


def test_bollinger_bands_are_symmetric_around_the_average():
    closes = pd.Series(ranging_series(120))
    mid, upper, lower = bollinger(closes, 20, 2.0)
    assert mid.iloc[:19].isna().all()
    assert mid.iloc[30] == pytest.approx(closes.iloc[11:31].mean())
    assert (upper - mid).iloc[30] == pytest.approx((mid - lower).iloc[30])
    assert (upper.dropna() > lower.dropna()).all()
    flat = pd.Series(np.full(60, 100.0))
    fmid, fup, flow = bollinger(flat, 20, 2.0)
    assert fup.iloc[-1] == pytest.approx(fmid.iloc[-1]) == pytest.approx(flow.iloc[-1])


def test_adx_is_higher_in_a_trend_than_in_a_range():
    trend = make_ohlcv([100 * 1.01 ** i for i in range(200)])
    flat = make_ohlcv(ranging_series(200, period=10))
    assert adx(trend, 14).iloc[-1] > adx(flat, 14).iloc[-1]
    assert adx(trend, 14).dropna().between(0, 100).all()
    assert adx(trend, 14).iloc[:13].isna().all()


# ---------------------------------------------------------------- breakout


def test_breakout_buys_when_price_clears_the_channel_high():
    s = BreakoutStrategy({"entry_period": 20, "exit_period": 10})
    df = make_ohlcv([100 * 1.006 ** i for i in range(200)])
    sigs = signals(s, df)
    buys = [x for x in sigs if x.action == Action.BUY]
    assert buys, "a steady uptrend must break the channel high"
    first = buys[0]
    assert "broke the 20-candle high" in first.reason
    assert first.stop_loss is not None and first.stop_loss > 0
    assert first.take_profit is None, "atr_tp_mult 0 means the channel exit closes the trade"
    assert 0 < first.confidence <= 1


def test_breakout_sells_when_price_loses_the_channel_low():
    # A narrow spread, so a steady drift actually clears the previous candles' extremes:
    # with a 0.5% wick and a 0.4% drift nothing would ever break out.
    s = BreakoutStrategy({"entry_period": 20, "exit_period": 10})
    df = make_ohlcv(trending_series(), spread=0.001)
    sigs = signals(s, df)
    sells = [i for i, x in enumerate(sigs) if x.action == Action.SELL]
    buys = [i for i, x in enumerate(sigs) if x.action == Action.BUY]
    assert buys and sells
    assert any(i > buys[0] for i in sells), "the downleg after the rally must produce an exit"
    assert "lost the 10-candle low" in sigs[sells[0]].reason


def test_breakout_stays_flat_in_a_range():
    s = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "min_breakout_atr": 1.0})
    df = make_ohlcv(ranging_series(300, amp=1.0, period=60))
    sigs = signals(s, df)
    assert Action.BUY not in actions(sigs), "a shallow range should not clear the channel by one ATR"


def test_breakout_min_break_size_filter_blocks_marginal_breaks():
    # No wicks, so each candle clears the last by exactly its drift: a break of about one ATR.
    df = make_ohlcv([100 * 1.0008 ** i for i in range(200)], spread=0.0)
    loose = signals(BreakoutStrategy({"entry_period": 20, "exit_period": 10}), df)
    strict = signals(BreakoutStrategy({"entry_period": 20, "exit_period": 10, "min_breakout_atr": 3.0}), df)
    assert Action.BUY in actions(loose)
    assert Action.BUY not in actions(strict)
    assert any("smaller than 3 ATR" in x.reason for x in strict)


def test_breakout_trend_filter_blocks_breakouts_below_the_trend_line():
    df = make_ohlcv(trending_series(), spread=0.001)
    s = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "trend_filter_period": 60})
    blocked = [x for x in signals(s, df) if "below the" in x.reason and "trend line" in x.reason]
    assert blocked, "breakouts inside the downleg should be refused by the trend filter"


def test_breakout_rejects_an_exit_period_longer_than_the_entry_period():
    with pytest.raises(ValueError, match="exit_period"):
        BreakoutStrategy({"entry_period": 10, "exit_period": 20})


def test_breakout_precompute_matches_candle_by_candle():
    df = make_ohlcv([100 * 1.004 ** i for i in range(260)])
    plain = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "trend_filter_period": 50})
    fast = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "trend_filter_period": 50})
    fast.precompute(df)
    assert [s.to_dict() for s in signals(plain, df)] == [s.to_dict() for s in signals(fast, df)]
    fast.clear_precomputed()
    assert fast._pre is None


# ---------------------------------------------------------------- mean reversion


def test_mean_reversion_buys_dips_in_a_rising_market():
    s = MeanReversionStrategy({"bb_period": 20, "rsi_buy_max": 45, "trend_filter_period": 50})
    df = make_ohlcv(dips_in_uptrend())
    sigs = signals(s, df)
    buys = [x for x in sigs if x.action == Action.BUY]
    assert buys, "pullbacks in an uptrend are exactly what this strategy should buy"
    first = buys[0]
    assert "below the lower band" in first.reason
    assert first.stop_loss is not None and first.stop_loss < first.take_profit


def test_mean_reversion_refuses_dips_while_the_trend_falls():
    df = make_ohlcv(falling_with_dips())
    with_filter = MeanReversionStrategy({"bb_period": 20, "rsi_buy_max": 45, "trend_filter_period": 50})
    without = MeanReversionStrategy({"bb_period": 20, "rsi_buy_max": 45, "trend_filter_period": 0})
    assert Action.BUY not in actions(signals(with_filter, df))
    assert Action.BUY in actions(signals(without, df)), "without the filter it buys into the downtrend"
    assert any("downtrend" in x.reason for x in signals(with_filter, df))


def test_mean_reversion_exits_at_the_chosen_band():
    df = make_ohlcv(dips_in_uptrend())
    common = {"bb_period": 20, "bb_std": 1.5, "trend_filter_period": 0}
    mid = MeanReversionStrategy({**common, "exit_band": "middle"})
    upper = MeanReversionStrategy({**common, "exit_band": "upper"})
    mid_sells = [x for x in signals(mid, df) if x.action == Action.SELL]
    upper_sells = [x for x in signals(upper, df) if x.action == Action.SELL]
    assert mid_sells and upper_sells
    assert "middle band" in mid_sells[0].reason and "upper band" in upper_sells[0].reason
    assert len(mid_sells) > len(upper_sells), "the middle band is reached more often than the upper one"


def test_mean_reversion_rsi_gate_blocks_shallow_dips():
    df = make_ohlcv(dips_in_uptrend())
    strict = MeanReversionStrategy({"bb_period": 20, "rsi_buy_max": 5, "trend_filter_period": 0})
    sigs = signals(strict, df)
    assert Action.BUY not in actions(sigs)
    assert any("is above 5" in x.reason for x in sigs)


def test_mean_reversion_stretch_filter_skips_a_falling_knife():
    df = make_ohlcv(dips_in_uptrend(shock=0.80))  # 20% in one candle: not a dip, a collapse
    common = {"bb_period": 20, "rsi_buy_max": 60, "trend_filter_period": 0}
    loose = MeanReversionStrategy(common)
    tight = MeanReversionStrategy({**common, "max_stretch_atr": 0.01})
    assert Action.BUY in actions(signals(loose, df))
    sigs = signals(tight, df)
    assert Action.BUY not in actions(sigs)
    assert any("too far to call it a bounce" in x.reason for x in sigs)


def test_mean_reversion_rejects_an_unknown_exit_band():
    with pytest.raises(ValueError, match="exit_band"):
        MeanReversionStrategy({"exit_band": "lower"})


def test_mean_reversion_precompute_matches_candle_by_candle():
    df = make_ohlcv(dips_in_uptrend(300))
    plain = MeanReversionStrategy({"bb_period": 20, "rsi_buy_max": 45, "trend_filter_period": 50})
    fast = MeanReversionStrategy({"bb_period": 20, "rsi_buy_max": 45, "trend_filter_period": 50})
    fast.precompute(df)
    assert [s.to_dict() for s in signals(plain, df)] == [s.to_dict() for s in signals(fast, df)]


def test_mean_reversion_default_trend_filter_fits_inside_its_warmup():
    s = MeanReversionStrategy({})
    df = make_ohlcv(dips_in_uptrend(s.warmup + 60))
    sig = s.on_candle(df)
    assert "trend filter not ready" not in sig.reason
    assert "warming up" not in sig.reason


# ---------------------------------------------------------------- regime switch


def _regime(**params) -> RegimeStrategy:
    base = {"trend_params": {"entry_period": 20, "exit_period": 10},
            "range_params": {"bb_period": 20, "rsi_buy_max": 45, "trend_filter_period": 50}}
    return RegimeStrategy({**base, **params})


def test_regime_uses_the_breakout_half_in_a_trend():
    s = _regime()
    df = make_ohlcv([100 * 1.008 ** i for i in range(s.warmup + 160)])
    buys = [x for x in signals(s, df) if x.action == Action.BUY]
    assert buys and buys[0].reason.startswith("[trend]")
    assert "adx=" in buys[0].reason


def test_regime_uses_the_mean_reversion_half_in_a_range():
    s = _regime(trend_above=60.0, range_below=60.0)  # force the ranging branch
    df = make_ohlcv(dips_in_uptrend(s.warmup + 200))
    buys = [x for x in signals(s, df) if x.action == Action.BUY]
    assert buys and buys[0].reason.startswith("[range]")


def test_regime_holds_when_adx_sits_between_the_thresholds():
    s = _regime(range_below=0.5, trend_above=99.0)
    df = make_ohlcv(dips_in_uptrend(s.warmup + 50))
    sigs = signals(s, df)
    assert set(actions(sigs)) == {Action.HOLD}
    assert all("neither a clear trend nor a clear range" in x.reason for x in sigs)


def test_regime_keeps_the_opening_half_in_charge_of_the_exit():
    s = _regime(trend_above=60.0, range_below=60.0)
    df = make_ohlcv(dips_in_uptrend(s.warmup + 200))
    owners, sigs = [], []
    for i in range(s.warmup, len(df)):
        sigs.append(s.on_candle(df.iloc[: i + 1]))
        owners.append(s.owner)  # ownership as it stands after the candle
    first_buy = next(i for i, x in enumerate(sigs) if x.action == Action.BUY)
    assert owners[first_buy] == "range"
    exit_at = next(i for i, x in enumerate(sigs[first_buy + 1:], first_buy + 1)
                   if x.action == Action.SELL)
    # Every candle between the entry and the exit is labelled by the half that opened it,
    # and the exit itself carries that label too (not a bare "exit").
    assert all(x.reason.startswith("[range]") for x in sigs[first_buy + 1: exit_at])
    assert all(o == "range" for o in owners[first_buy:exit_at])
    assert sigs[exit_at].reason.startswith("[range]")
    assert owners[exit_at] is None, "ownership is released on the exit signal"


def test_regime_ownership_survives_a_regime_flip_mid_trade():
    s = _regime(trend_above=60.0, range_below=60.0)
    df = make_ohlcv(dips_in_uptrend(s.warmup + 200))
    for i in range(s.warmup, len(df)):
        if s.on_candle(df.iloc[: i + 1]).action == Action.BUY:
            break
    assert s.owner == "range"
    s.p.trend_above, s.p.range_below = 0.1, 0.1  # the market now looks like a trend
    out = s.on_candle(df.iloc[: i + 2])
    assert s.owner == "range", "a flip must not hand an open position to the other half"
    assert out.reason.startswith("[range]")


def test_regime_releases_ownership_when_the_engine_has_no_position():
    s = _regime(trend_above=60.0, range_below=60.0)
    df = make_ohlcv(dips_in_uptrend(s.warmup + 200))
    for i in range(s.warmup, len(df)):
        if s.on_candle(df.iloc[: i + 1], context={"position": {"qty": 1.0}}).action == Action.BUY:
            break
    assert s.owner == "range"
    # The engine reports a flat account: the entry was refused, or the stop already closed it.
    s.on_candle(df.iloc[: i + 2], context={"position": None})
    assert s.owner is None


def test_regime_exposes_the_owner_and_the_adx_column():
    s = _regime()
    df = make_ohlcv([100 * 1.008 ** i for i in range(s.warmup + 160)])
    s.precompute(df)
    ind = s.indicators(df)
    assert "adx" in ind.columns and len(ind) == len(df)
    assert not ind["adx"].tail(50).isna().any()
    assert s.describe()["regime_owner"] is None
    assert s.describe()["name"] == "regime"


def test_regime_rejects_thresholds_in_the_wrong_order():
    with pytest.raises(ValueError, match="range_below"):
        RegimeStrategy({"trend_above": 10.0, "range_below": 30.0})


def test_regime_precompute_matches_candle_by_candle():
    df = make_ohlcv([100 * 1.006 ** i for i in range(360)])
    plain, fast = _regime(), _regime()
    fast.precompute(df)
    assert [s.to_dict() for s in signals(plain, df)] == [s.to_dict() for s in signals(fast, df)]


# ---------------------------------------------------------------- benchmark


def test_buy_hold_buys_exactly_once():
    s = BuyHoldStrategy({})
    df = make_ohlcv(trending_series())
    sigs = [s.on_candle(df.iloc[: i + 1]) for i in range(len(df))]
    buys = [i for i, x in enumerate(sigs) if x.action == Action.BUY]
    assert len(buys) == 1 and buys[0] == s.warmup - 1
    assert Action.SELL not in actions(sigs)
    assert sigs[buys[0]].stop_loss is None


def test_buy_hold_optional_stop():
    s = BuyHoldStrategy({"warmup": 3, "stop_loss_pct": 20.0})
    df = make_ohlcv([100.0, 101.0, 102.0, 103.0])
    sig = s.on_candle(df.iloc[:3])
    assert sig.action == Action.BUY
    assert sig.stop_loss == pytest.approx(102.0 * 0.8)


# ---------------------------------------------------------------- registry


def test_every_new_strategy_is_registered_and_constructible():
    for name in ("breakout", "mean_reversion", "regime", "buy_hold"):
        assert name in STRATEGIES
        s = get_strategy(name, {})
        assert s.name == name and s.warmup >= 1
        assert s.describe()["name"] == name


def test_unknown_parameters_are_dropped_by_the_registry_and_rejected_by_the_class():
    from bot.strategy import STRATEGIES as REG

    for name in ("breakout", "mean_reversion", "regime", "buy_hold"):
        # The registry is forgiving, because a config file carries settings from whichever
        # strategy was selected before...
        assert get_strategy(name, {"nonsense_parameter": 1}).name == name
        # ...while the class itself stays strict, so a typo in code is caught at once.
        with pytest.raises(Exception):
            REG[name]({"nonsense_parameter": 1})


# ------------------------------------------------- leftovers from another strategy


def test_parameters_of_a_previous_strategy_are_dropped_not_fatal():
    """Switching from `ai` back to `ema_rsi` leaves the AI's settings in config.yaml.
    The bot must run and say what it ignored, not refuse to start."""
    from bot.strategy import prune_params

    leftovers = {"model": "claude-haiku-4-5", "only_on_technical_setup": True,
                 "fallback_to_technical": True, "mode": "balanced", "ema_fast": 8}
    clean, dropped = prune_params("ema_rsi", leftovers)
    assert clean == {"ema_fast": 8}
    assert sorted(dropped) == ["fallback_to_technical", "mode", "model", "only_on_technical_setup"]
    assert get_strategy("ema_rsi", leftovers).p.ema_fast == 8
    assert leftovers["model"] == "claude-haiku-4-5", "the caller's dict must not be modified"


def test_regime_drops_leftovers_inside_its_two_halves():
    s = get_strategy("regime", {"trend_params": {"entry_period": 25, "rsi_buy_min": 40.0},
                                "range_params": {"bb_period": 30, "ema_fast": 8}})
    assert s.trend.p.entry_period == 25 and s.range.p.bb_period == 30


def test_a_genuinely_wrong_value_still_stops_the_bot():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        get_strategy("ema_rsi", {"ema_fast": -5})
    with pytest.raises(ValueError, match="exit_period"):
        get_strategy("breakout", {"entry_period": 5, "exit_period": 20})


# ------------------------------------------------- breakout entry quality


def _breaking_frame(strength: float = 1.0, volume_mult: float = 1.0, candles: int = 60):
    """A flat channel and then one candle that closes above it, shaped to order."""
    closes = [100.0] * candles + [110.0]
    df = make_ohlcv(closes, spread=0.0)
    last = df.index[-1]
    high, low = 112.0, 100.0
    df.loc[last, "high"], df.loc[last, "low"] = high, low
    df.loc[last, "close"] = low + strength * (high - low)
    df.loc[last, "volume"] = 10.0 * volume_mult
    return df


def test_a_breakout_that_closes_at_the_bottom_of_its_candle_is_refused():
    df = _breaking_frame(strength=0.2)
    weak = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "close_strength_min": 0.6})
    strong = BreakoutStrategy({"entry_period": 20, "exit_period": 10})
    refused = weak.on_candle(df)
    assert refused.action == Action.HOLD and "up its own candle" in refused.reason
    assert strong.on_candle(df).action == Action.BUY, "without the filter it is a plain breakout"
    assert BreakoutStrategy({"entry_period": 20, "exit_period": 10, "close_strength_min": 0.6}).on_candle(
        _breaking_frame(strength=0.9)).action == Action.BUY


def test_a_breakout_on_thin_volume_is_refused():
    quiet = _breaking_frame(volume_mult=0.5)
    s = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "volume_min_ratio": 1.2})
    out = s.on_candle(quiet)
    assert out.action == Action.HOLD and "channel average" in out.reason
    assert s.on_candle(_breaking_frame(volume_mult=3.0)).action == Action.BUY


def test_confirmation_waits_for_the_close_to_hold():
    two = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "confirm_candles": 2})
    one_bar = _breaking_frame()
    held = two.on_candle(one_bar)
    assert held.action == Action.HOLD and "1 of 2 candles" in held.reason

    # A second candle that also closes above the (still flat) channel completes it.
    closes = [100.0] * 60 + [110.0, 111.0]
    df = make_ohlcv(closes, spread=0.0)
    assert two.on_candle(df).action == Action.BUY
    assert BreakoutStrategy({"entry_period": 20, "exit_period": 10, "confirm_candles": 3}).on_candle(df).action == Action.HOLD


def test_the_structural_stop_sits_under_the_channel_and_never_wider_than_the_atr_stop():
    df = _breaking_frame()
    atr_stop = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "atr_stop_mult": 2.0}).on_candle(df)
    structural = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "atr_stop_mult": 2.0,
                                   "stop_at_channel": True, "stop_channel_buffer_atr": 0.25}).on_candle(df)
    assert atr_stop.action == structural.action == Action.BUY
    assert structural.stop_loss >= atr_stop.stop_loss, "structure never widens the risk"
    assert structural.stop_loss < float(df["close"].iloc[-1])


def test_the_volume_filter_stands_aside_when_there_is_no_volume_data():
    """Some CSV exports carry no volume column and the loader fills zeros. A filter with
    nothing to measure must not refuse every entry for the life of the file."""
    df = _breaking_frame()
    df["volume"] = 0.0
    s = BreakoutStrategy({"entry_period": 20, "exit_period": 10, "volume_min_ratio": 1.5})
    out = s.on_candle(df)
    assert out.action == Action.BUY, out.reason
    assert "volume" not in out.reason.split(";")[0], "and it does not claim a confirmation it cannot see"
