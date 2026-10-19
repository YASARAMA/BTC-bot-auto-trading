"""No look-ahead: every feature, regime and signal of candle t is computable at the close of t.

Two independent checks. Truncation parity recomputes everything on the history up to t and
requires the row for t to be identical to the one computed on the full history. Future
perturbation changes every candle after t and requires nothing at or before t to change.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.config import Settings
from app.strategies.pipeline import context, prepare, signals
from app.strategies.registry import STRATEGIES, build
from tests.scenarios import ranging, squeeze_then_breakout, trending

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "samples" / "binance_BTCUSDT_1h_2020-11_2021-05.csv"


def full_state(df: pd.DataFrame, tf: str, confirm: list[str], s: Settings) -> pd.DataFrame:
    view = prepare(df, "X/USDT", tf, confirm, s)
    parts = [view.features, view.regime]
    ctx = context(s, tf, confirm)
    for name in STRATEGIES:
        sig = signals(view, build(name), ctx).drop(columns="ts").add_prefix(f"{name}.")
        parts.append(sig)
    return pd.concat(parts, axis=1)


def rows_equal(a: pd.Series, b: pd.Series) -> None:
    assert list(a.index) == list(b.index)
    for key in a.index:
        x, y = a[key], b[key]
        if isinstance(x, float) and isinstance(y, float) and np.isnan(x) and np.isnan(y):
            continue
        if isinstance(x, (float, np.floating)) and isinstance(y, (float, np.floating)):
            assert x == pytest.approx(y, rel=1e-12, abs=1e-12), key
        else:
            assert x == y, key


CASES = [
    ("trend", lambda: trending(1), "15m", ["1h", "4h"], [905, 1037, 1222, 1399]),
    ("range", lambda: ranging(), "15m", ["1h", "4h"], [930, 1111, 1300]),
    ("squeeze", lambda: squeeze_then_breakout(), "15m", ["1h", "4h"], [1000, 1159, 1160]),
]


@pytest.mark.parametrize("name, make, tf, confirm, cuts", CASES)
def test_truncation_parity(name, make, tf, confirm, cuts) -> None:
    s = Settings()
    df = make()
    full = full_state(df, tf, confirm, s)
    for cut in cuts:
        part = full_state(df.iloc[: cut + 1], tf, confirm, s)
        rows_equal(full.iloc[cut], part.iloc[cut])


@pytest.mark.parametrize("name, make, tf, confirm, cuts", CASES)
def test_future_perturbation_changes_nothing_before(name, make, tf, confirm, cuts) -> None:
    s = Settings()
    df = make()
    cut = cuts[0]
    altered = df.copy()
    rng = np.random.default_rng(1)
    factor = rng.uniform(0.5, 1.5, len(df) - cut - 1)
    for col in ("open", "high", "low", "close"):
        altered.loc[cut + 1:, col] = altered.loc[cut + 1:, col].to_numpy() * factor
    altered.loc[cut + 1:, "volume"] = altered.loc[cut + 1:, "volume"].to_numpy() * 10
    a = full_state(df, tf, confirm, s).iloc[: cut + 1]
    b = full_state(altered, tf, confirm, s).iloc[: cut + 1]
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)


@pytest.mark.skipif(not SAMPLE.exists(), reason="real sample not present")
def test_parity_on_real_binance_sample() -> None:
    s = Settings()
    df = pd.read_csv(SAMPLE)
    full = full_state(df, "1h", ["4h", "1d"], s)
    rng = np.random.default_rng(42)
    for cut in sorted(rng.integers(1300, len(df) - 1, 6)):
        part = full_state(df.iloc[: int(cut) + 1], "1h", ["4h", "1d"], s)
        rows_equal(full.iloc[int(cut)], part.iloc[int(cut)])
