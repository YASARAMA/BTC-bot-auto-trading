"""Entry and exit rules of the four strategies on hand-built feature frames."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config import Settings
from app.regimes.detector import RANGE, TREND_DOWN, TREND_UP, UNCERTAIN
from app.strategies.base import StrategyContext
from app.strategies.pipeline import context, prepare, signals
from app.strategies.registry import STRATEGIES, build
from tests.scenarios import squeeze_then_breakout

CTX = StrategyContext(round_trip_cost_pct=0.32, signal_timeframe="15m", confirm_timeframes=("1h", "4h"))


def regime_frame(n: int, label: str = TREND_UP, conf: float = 0.8, direction: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"regime": [label] * n, "regime_confidence": [conf] * n, "regime_reason": [""] * n,
                         "regime_direction": [direction] * n})


def trend_frame() -> pd.DataFrame:
    n = 30
    f = pd.DataFrame({
        "ts": np.arange(n, dtype="int64") * 900_000, "open": [100.8] * n, "high": [101.2] * n, "low": [100.5] * n,
        "close": [101.0] * n, "volume": [100.0] * n, "ema_fast": [100.0] * n, "ema_mid": [98.0] * n,
        "ema_slow": [95.0] * n, "atr": [1.0] * n, "rsi": [60.0] * n, "h1h_trend": [1.0] * n, "h4h_trend": [1.0] * n,
    })
    f.loc[26, "low"] = 100.1           # touched the fast EMA zone
    f.loc[27, "rsi"] = 48.0            # RSI cooled during the pullback
    f.loc[29, ["open", "high", "low", "close"]] = [100.6, 101.5, 100.5, 101.4]   # closes above previous high
    f["extension_atr"] = (f["close"] - f["ema_fast"]) / f["atr"]
    return f


def test_trend_following_entry_levels() -> None:
    f = trend_frame()
    sig = build("trend_following").signals(f, regime_frame(len(f)), CTX)
    assert sig["entry"].tolist() == [False] * 29 + [True]
    last = sig.iloc[-1]
    assert last["stop"] == pytest.approx(100.1 - 0.3)                 # swing low minus buffer
    assert last["target"] == pytest.approx(101.4 + 2.0 * (101.4 - 99.8))
    assert 0 < last["confidence"] <= 1


@pytest.mark.parametrize("change, why", [
    (lambda f, r: f.__setitem__("h4h_trend", -1.0), "higher timeframe down"),
    (lambda f, r: f.__setitem__("rsi", 60.0), "no RSI pullback"),
    (lambda f, r: r.__setitem__("regime", RANGE), "wrong regime"),
    (lambda f, r: f.loc.__setitem__((29, "extension_atr"), 2.0), "over-extended"),
    (lambda f, r: f.loc.__setitem__((29, "open"), 101.45), "bearish confirmation candle"),
])
def test_trend_following_rejections(change, why) -> None:
    f, r = trend_frame(), regime_frame(30)
    change(f, r)
    sig = build("trend_following").signals(f, r, CTX)
    assert not sig["entry"].iloc[-1], why


def test_trend_following_cost_filter() -> None:
    f = trend_frame()
    expensive = StrategyContext(round_trip_cost_pct=2.0, confirm_timeframes=("1h", "4h"))
    assert not build("trend_following").signals(f, regime_frame(30), expensive)["entry"].iloc[-1]


def test_trend_following_exit_when_trend_breaks() -> None:
    f = trend_frame()
    f.loc[20, "close"] = 97.5
    r = regime_frame(30)
    r.loc[25, "regime"] = TREND_DOWN
    sig = build("trend_following").signals(f, r, CTX)
    assert sig.loc[20, "exit_signal"] and "mid EMA" in sig.loc[20, "exit_reason"]
    assert sig.loc[25, "exit_signal"] and "trend_down" in sig.loc[25, "exit_reason"]


def breakout_frame() -> pd.DataFrame:
    n = 60
    f = pd.DataFrame({
        "ts": np.arange(n, dtype="int64") * 900_000, "open": [100.0] * n, "high": [101.5] * n, "low": [99.0] * n,
        "close": [100.2] * n, "volume": [100.0] * n, "atr": [1.0] * n, "rsi": [55.0] * n, "rel_volume": [1.0] * n,
        "close_loc": [0.5] * n, "range_atr": [1.0] * n, "avg_quote_volume": [5e6] * n, "h1h_trend": [1.0] * n,
        "h4h_trend": [0.0] * n,
    })
    f.loc[59, ["open", "high", "low", "close", "rsi", "rel_volume", "close_loc", "range_atr"]] = [
        100.4, 101.9, 100.3, 101.8, 65.0, 2.0, 0.94, 1.5]
    return f


def test_momentum_breakout_entry() -> None:
    f = breakout_frame()
    sig = build("momentum_breakout").signals(f, regime_frame(60), CTX)
    assert sig["entry"].sum() == 1 and sig["entry"].iloc[-1]
    assert sig["stop"].iloc[-1] == pytest.approx(101.0)                # range high minus buffer
    assert sig["target"].iloc[-1] == pytest.approx(101.8 + 2.5 * 0.8)


@pytest.mark.parametrize("col, value", [("rel_volume", 1.1), ("close_loc", 0.4), ("rsi", 85.0), ("h4h_trend", -1.0),
                                        ("range_atr", 5.0), ("avg_quote_volume", 1e3)])
def test_momentum_breakout_filters(col, value) -> None:
    f = breakout_frame()
    f.loc[59, col] = value
    if col == "h4h_trend":
        f[col] = value
    assert not build("momentum_breakout").signals(f, regime_frame(60), CTX)["entry"].iloc[-1]


def test_momentum_breakout_skips_late_entries() -> None:
    f = breakout_frame()
    f.loc[59, ["high", "close"]] = [104.0, 103.9]                       # 2.4 ATR above the range already
    assert not build("momentum_breakout").signals(f, regime_frame(60), CTX)["entry"].iloc[-1]


def mean_reversion_frame() -> tuple[pd.DataFrame, pd.DataFrame]:
    n = 30
    f = pd.DataFrame({
        "ts": np.arange(n, dtype="int64") * 900_000, "open": [100.0] * n, "high": [100.4] * n, "low": [99.6] * n,
        "close": [100.0] * n, "volume": [100.0] * n, "atr": [0.8] * n, "rsi": [50.0] * n, "bb_mid": [100.0] * n,
        "bb_std": [1.0] * n, "bb_lower": [98.0] * n, "h1h_trend": [0.0] * n, "h4h_trend": [0.0] * n,
    })
    f.loc[28, ["open", "high", "low", "close", "rsi"]] = [99.5, 99.6, 97.5, 97.8, 35.0]   # stretched, weak
    f.loc[29, ["open", "high", "low", "close"]] = [98.0, 98.6, 97.9, 98.4]               # back inside, bullish
    r = regime_frame(n, RANGE, 0.8, 0)
    r.loc[28, "regime"] = UNCERTAIN      # the stretch candle itself left the range
    return f, r


def test_mean_reversion_entry_and_target() -> None:
    f, r = mean_reversion_frame()
    sig = build("mean_reversion").signals(f, r, CTX)
    assert sig["entry"].tolist() == [False] * 29 + [True]
    assert sig["stop"].iloc[-1] == pytest.approx(97.5 - 0.4)
    assert sig["target"].iloc[-1] == pytest.approx(100.0)               # the mean


def test_mean_reversion_requires_an_established_range() -> None:
    f, r = mean_reversion_frame()
    r.loc[:27, "regime"] = TREND_UP                                     # no range before the stretch
    assert not build("mean_reversion").signals(f, r, CTX)["entry"].iloc[-1]
    f, r = mean_reversion_frame()
    r.loc[29, "regime"] = TREND_DOWN                                    # not a range now
    assert not build("mean_reversion").signals(f, r, CTX)["entry"].iloc[-1]
    f, r = mean_reversion_frame()
    f["h4h_trend"] = -1.0                                               # strong higher-timeframe downtrend
    assert not build("mean_reversion").signals(f, r, CTX)["entry"].iloc[-1]


def test_mean_reversion_exits_at_the_mean() -> None:
    f, r = mean_reversion_frame()
    f.loc[10, "close"] = 100.5
    sig = build("mean_reversion").signals(f, r, CTX)
    assert sig.loc[10, "exit_signal"] and sig.loc[10, "exit_reason"] == "close reached the mean"


def test_volatility_breakout_on_scenario() -> None:
    s = Settings()
    df = squeeze_then_breakout()
    view = prepare(df, "BTC/USDT", "15m", ["1h", "4h"], s)
    strat = build("volatility_breakout")
    i = len(df) - 1
    # With cheap execution the setup qualifies on the expansion candle and nowhere in the squeeze.
    cheap = StrategyContext(round_trip_cost_pct=0.08, confirm_timeframes=("1h", "4h"))
    sig = signals(view, strat, cheap)
    assert sig["entry"].iloc[i], strat.explain(view.features, view.regime, cheap, i)
    assert sig["entry"].iloc[-40:-1].sum() == 0
    close = df["close"].iloc[i]
    assert sig["stop"].iloc[i] < close < sig["target"].iloc[i]
    assert sig["invalidation"].iloc[i] < close
    # At the default cost assumptions the move to the target after such a tight squeeze does not
    # pay three times the round-trip cost, so the same candle is refused.
    default = context(s, "15m", ["1h", "4h"])
    assert not signals(view, strat, default)["entry"].iloc[i]
    failed = [c["condition"] for c in strat.explain(view.features, view.regime, default, i)["checks"] if not c["passed"]]
    assert failed == ["stop distance within limits and the target pays the costs"]
    too_far = squeeze_then_breakout(bump=3.0)                            # most of the move already happened
    view2 = prepare(too_far, "BTC/USDT", "15m", ["1h", "4h"], s)
    assert not signals(view2, strat, cheap)["entry"].iloc[-1]


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_every_strategy_exposes_its_specification(name) -> None:
    st = build(name)
    spec = st.spec()
    for key in ("required_regimes", "entry_rules", "exit_rules", "stop_rule", "target_rule", "hypothesis",
                "version", "params", "params_schema"):
        assert spec[key], key
    assert spec["direction"].startswith("long only")
    assert st.version.startswith(f"{name}@")
    assert build(name, {}).version == st.version                        # same params, same version
    other = dict(st.params_dict)
    key = st.tunable[0]
    other[key] = other[key] * 2 if isinstance(other[key], (int, float)) else other[key]
    assert build(name, other).version != st.version                     # new params, new version


def test_unknown_or_invalid_parameters_rejected() -> None:
    from app.core.errors import InvalidRequest

    with pytest.raises(InvalidRequest):
        build("nope")
    with pytest.raises(InvalidRequest):
        build("trend_following", {"target_r": -1})
    with pytest.raises(InvalidRequest):
        build("momentum_breakout", {"unknown": 1})
