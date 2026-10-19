"""Deterministic synthetic market shapes for regime and strategy tests (test fixtures only)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tests.fakes import synthetic_candles

M15 = 900_000


def trending(direction: int = 1, n: int = 1400, seed: int = 3) -> pd.DataFrame:
    return synthetic_candles(n, drift=0.0012 * direction, vol=0.002, seed=seed)


def ranging(n: int = 1400, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = x[i - 1] * 0.9 + rng.normal(0, 0.002)
    closes = 30_000 * np.exp(x)
    opens = np.r_[30_000, closes[:-1]]
    spread = np.abs(rng.normal(0, 0.001, n)) * closes
    start = 1_700_000_000_000 - 1_700_000_000_000 % M15
    return pd.DataFrame({"ts": start + np.arange(n, dtype="int64") * M15, "open": opens,
                         "high": np.maximum(opens, closes) + spread, "low": np.minimum(opens, closes) - spread,
                         "close": closes, "volume": rng.uniform(50, 150, n)})


def squeeze_then_breakout(bump: float = 1.6, vol_mult: float = 4.0, seed: int = 4, n_trend: int = 1100,
                          n_squeeze: int = 60) -> pd.DataFrame:
    """A gentle uptrend, a tight sideways squeeze, then one completed expansion candle."""
    base = synthetic_candles(n_trend, drift=0.0004, vol=0.003, seed=seed)
    ts0 = int(base["ts"].iloc[-1])
    rng = np.random.default_rng(seed)
    anchor = float(base["close"].iloc[-1])
    rows, prev = [], anchor
    for i in range(n_squeeze):
        # Oscillate around a fixed level: a genuinely tight box, not a slow random walk.
        o = prev
        c = anchor * (1 + rng.normal(0, 0.0004))
        rows.append([ts0 + (i + 1) * M15, o, max(o, c) * 1.0003, min(o, c) * 0.9997, c, rng.uniform(60, 90)])
        prev = c
    sq = pd.DataFrame(rows, columns=base.columns)
    df = pd.concat([base, sq], ignore_index=True)
    tr = float((sq["high"] - sq["low"]).mean())
    o = float(df["close"].iloc[-1])
    box_high = float(df["high"].iloc[-12:].max())
    c = max(box_high, o) + bump * tr
    breakout = pd.DataFrame([[ts0 + (n_squeeze + 1) * M15, o, c + 0.1 * tr, o - 0.2 * tr, c,
                              float(sq["volume"].mean() * vol_mult)]], columns=base.columns)
    return pd.concat([df, breakout], ignore_index=True)
