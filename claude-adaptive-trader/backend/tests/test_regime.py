from __future__ import annotations

import pandas as pd
import pytest

from app.config import Settings
from app.regimes.detector import BREAKOUT, RANGE, REGIMES, TREND_DOWN, TREND_UP, UNCERTAIN, explain
from app.strategies.pipeline import prepare
from tests.scenarios import ranging, squeeze_then_breakout, trending

CONFIRM = ["1h", "4h"]


def regimes(df: pd.DataFrame, settings: Settings | None = None):
    return prepare(df, "BTC/USDT", "15m", CONFIRM, settings or Settings())


def share(v, name: str, start: int = 900) -> float:
    return float((v.regime["regime"].iloc[start:] == name).mean())


def test_uptrend_is_classified_up() -> None:
    v = regimes(trending(1))
    assert share(v, TREND_UP) > 0.6 and share(v, TREND_DOWN) == 0 and share(v, RANGE) == 0


def test_downtrend_is_classified_down() -> None:
    v = regimes(trending(-1))
    assert share(v, TREND_DOWN) > 0.6 and share(v, TREND_UP) == 0


def test_sideways_market_is_range_or_abstains() -> None:
    v = regimes(ranging())
    assert share(v, RANGE) > 0.2
    assert share(v, TREND_UP) + share(v, TREND_DOWN) < 0.05


def test_squeeze_then_expansion_is_breakout() -> None:
    v = regimes(squeeze_then_breakout())
    last = v.regime.iloc[-1]
    assert last["regime"] == BREAKOUT and last["regime_direction"] == 1
    assert v.regime["regime"].iloc[-30:-1].ne(BREAKOUT).all()      # the squeeze itself is not a breakout


def test_warmup_rows_abstain_with_a_reason() -> None:
    v = regimes(trending(1, n=300))
    assert (v.regime["regime"] == UNCERTAIN).all()
    assert v.regime["regime_reason"].iloc[10] == "indicators warming up"


def test_volatility_shock_abstains() -> None:
    df = squeeze_then_breakout(bump=12.0)            # a candle far beyond the validated range
    v = regimes(df)
    last = v.regime.iloc[-1]
    assert last["regime"] == UNCERTAIN and "shock" in last["regime_reason"]


def test_low_confidence_abstains() -> None:
    df = ranging()
    base = regimes(df)
    strict = regimes(df, Settings.model_validate({"regime": {"min_confidence": 0.85}}))
    weak = (base.regime["regime"] != UNCERTAIN) & (base.regime["regime_confidence"] < 0.85)
    assert weak.any(), "scenario must contain low-confidence classifications"
    assert (strict.regime.loc[weak, "regime"] == UNCERTAIN).all()
    assert strict.regime.loc[weak, "regime_reason"].str.contains("below 0.85").all()
    confident = (base.regime["regime"] != UNCERTAIN) & ~weak
    assert (strict.regime.loc[confident, "regime"] == base.regime.loc[confident, "regime"]).all()


def test_thresholds_are_respected() -> None:
    # ADX never exceeds 100, so with this threshold no candle can be a trend.
    no_trends = Settings.model_validate({"regime": {"trend_adx_min": 100.0, "range_adx_max": 18.0}})
    v = regimes(trending(1), no_trends)
    assert share(v, TREND_UP) == 0 and share(v, TREND_DOWN) == 0


def test_every_label_is_known_and_confidence_bounded() -> None:
    v = regimes(trending(1))
    assert set(v.regime["regime"].unique()) <= set(REGIMES)
    assert v.regime["regime_confidence"].between(0, 1).all()


def test_explain_lists_features_and_thresholds() -> None:
    s = Settings()
    v = regimes(trending(1), s)
    e = explain(v.features, len(v.features) - 1, s.regime, CONFIRM)
    assert e["features"]["adx"] is not None and "trend_adx_min" in e["thresholds"]
    assert set(e["features"]) >= {"htf_1h", "htf_4h", "slope_mid_atr"}


@pytest.mark.parametrize("cut", [950, 1100, 1250])
def test_regime_uses_no_future_candles(cut: int) -> None:
    df = trending(1)
    full = regimes(df)
    part = regimes(df.iloc[: cut + 1])
    assert full.regime.iloc[cut].to_dict() == part.regime.iloc[cut].to_dict()
