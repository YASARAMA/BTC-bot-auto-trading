"""Indicators against straightforward reference implementations written from the definitions."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.indicators import (adx, atr, bollinger, close_location, dmi, donchian, ema, percentile_rank,
                            relative_volume, rma, rsi, sma, true_range)
from tests.fakes import synthetic_candles

NAN = float("nan")


def ref_seeded(x: list[float], n: int, alpha: float, start: int = 0) -> list[float]:
    out = [NAN] * len(x)
    if len(x) - start < n:
        return out
    s = sum(x[start:start + n]) / n
    out[start + n - 1] = s
    for i in range(start + n, len(x)):
        s = alpha * x[i] + (1 - alpha) * s
        out[i] = s
    return out


def ref_rsi(c: list[float], n: int) -> list[float]:
    out = [NAN] * len(c)
    d = [c[i] - c[i - 1] for i in range(1, len(c))]
    g = [max(v, 0.0) for v in d]
    lo = [max(-v, 0.0) for v in d]
    if len(d) < n:
        return out
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n

    def val(a: float, b: float) -> float:
        return 100.0 if b == 0 else (0.0 if a == 0 else 100 - 100 / (1 + a / b))

    out[n] = val(ag, al)
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
        out[i + 1] = val(ag, al)
    return out


def ref_tr(h, lo, c, first=True):
    tr = [h[0] - lo[0] if first else NAN]
    for i in range(1, len(h)):
        tr.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    return tr


def ref_adx(h, lo, c, n):
    size = len(h)
    pdm, mdm = [NAN], [NAN]
    for i in range(1, size):
        up, down = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
    tr = ref_tr(h, lo, c, first=False)
    sp, sm, st = (ref_seeded(v, n, 1 / n, start=1) for v in (pdm, mdm, tr))
    dx = [NAN] * size
    for i in range(size):
        if math.isnan(st[i]):
            continue
        p = 100 * sp[i] / st[i] if st[i] else 0.0
        m = 100 * sm[i] / st[i] if st[i] else 0.0
        dx[i] = 100 * abs(p - m) / (p + m if p + m else 1.0)
    first = next(i for i, v in enumerate(dx) if not math.isnan(v))
    return ref_seeded(dx, n, 1 / n, start=first)


def close_enough(a, b, tol=1e-9):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b)), "warm-up (NaN) positions differ"
    mask = ~np.isnan(a)
    np.testing.assert_allclose(a[mask], b[mask], rtol=tol, atol=tol)


@pytest.fixture(scope="module")
def candles() -> pd.DataFrame:
    return synthetic_candles(400, "15m", seed=11, vol=0.006)


def test_ema_hand_example() -> None:
    out = ema(pd.Series([1.0, 2, 3, 4, 5]), 3).tolist()
    assert math.isnan(out[0]) and math.isnan(out[1])
    assert out[2:] == [2.0, 3.0, 4.0]                 # SMA seed 2, alpha 0.5


@pytest.mark.parametrize("period", [2, 9, 20, 50])
def test_ema_rma_sma_match_reference(candles, period) -> None:
    x = candles["close"].tolist()
    close_enough(ema(candles["close"], period), ref_seeded(x, period, 2 / (period + 1)))
    close_enough(rma(candles["close"], period), ref_seeded(x, period, 1 / period))
    close_enough(sma(candles["close"], period), pd.Series(x).rolling(period).mean())


def test_ema_of_constant_is_constant() -> None:
    assert ema(pd.Series([7.0] * 50), 10).dropna().eq(7.0).all()


@pytest.mark.parametrize("period", [2, 14])
def test_rsi_matches_reference(candles, period) -> None:
    close_enough(rsi(candles["close"], period), ref_rsi(candles["close"].tolist(), period))


def test_rsi_extremes_and_bounds(candles) -> None:
    assert rsi(pd.Series(np.arange(1.0, 40.0)), 14).dropna().eq(100.0).all()
    assert rsi(pd.Series(np.arange(40.0, 1.0, -1)), 14).dropna().eq(0.0).all()
    r = rsi(candles["close"], 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_true_range_and_atr_match_reference(candles) -> None:
    h, lo, c = (candles[k].tolist() for k in ("high", "low", "close"))
    close_enough(true_range(candles), ref_tr(h, lo, c))
    close_enough(atr(candles, 14), ref_seeded(ref_tr(h, lo, c), 14, 1 / 14))


def test_atr_simple_cases() -> None:
    flat = pd.DataFrame({"high": [10.0] * 30, "low": [10.0] * 30, "close": [10.0] * 30})
    assert atr(flat, 14).dropna().eq(0.0).all()
    steady = pd.DataFrame({"high": [11.0] * 30, "low": [9.0] * 30, "close": [10.0] * 30})
    assert np.allclose(atr(steady, 14).dropna(), 2.0)
    gap = pd.DataFrame({"high": [10.0, 21.0], "low": [9.0, 20.0], "close": [9.5, 20.5]})
    assert true_range(gap).iloc[1] == pytest.approx(11.5)    # the gap counts as range


def test_adx_matches_reference(candles) -> None:
    h, lo, c = (candles[k].tolist() for k in ("high", "low", "close"))
    close_enough(adx(candles, 14), ref_adx(h, lo, c, 14))
    first = adx(candles, 14).first_valid_index()
    assert first == 2 * 14 - 1


def test_adx_strong_trend_high_flat_low() -> None:
    up = pd.DataFrame({"high": np.arange(100.0, 200.0) + 1, "low": np.arange(100.0, 200.0) - 1,
                       "close": np.arange(100.0, 200.0)})
    d = dmi(up, 14)
    assert d["adx"].iloc[-1] > 50 and (d["plus_di"].iloc[-1] > d["minus_di"].iloc[-1])


def test_bollinger(candles) -> None:
    bb = bollinger(candles["close"], 20, 2.0)
    c = candles["close"]
    mid = c.rolling(20).mean()
    sd = c.rolling(20).apply(lambda w: float(np.std(w, ddof=0)), raw=True)
    close_enough(bb["bb_mid"], mid)
    close_enough(bb["bb_upper"], mid + 2 * sd)
    close_enough(bb["bb_lower"], mid - 2 * sd)
    flat = bollinger(pd.Series([5.0] * 30), 20)
    assert flat["bb_width"].dropna().eq(0).all() and flat["bb_pct"].dropna().eq(0.5).all()


def test_relative_volume_excludes_current_candle() -> None:
    v = pd.Series([10.0] * 25 + [50.0])
    rel = relative_volume(v, 20)
    assert rel.iloc[-1] == pytest.approx(5.0)              # 50 / mean of the previous 20
    assert rel.iloc[:20].isna().all()
    assert relative_volume(pd.Series([0.0] * 30), 20).isna().all()   # no baseline -> no value


def test_percentile_rank_reference(candles) -> None:
    x = candles["close"].to_numpy()
    w = 50
    expected = [NAN] * (w - 1) + [100 * np.mean(x[i - w + 1:i] <= x[i]) for i in range(w - 1, len(x))]
    close_enough(percentile_rank(candles["close"], w), expected)


def test_donchian_uses_previous_candles_only(candles) -> None:
    dc = donchian(candles, 10)
    i = 50
    assert dc["dc_high"].iloc[i] == candles["high"].iloc[i - 10:i].max()
    assert dc["dc_low"].iloc[i] == candles["low"].iloc[i - 10:i].min()


def test_close_location() -> None:
    df = pd.DataFrame({"high": [10.0, 10.0], "low": [0.0, 10.0], "close": [7.5, 10.0]})
    assert close_location(df).tolist() == [0.75, 0.5]
