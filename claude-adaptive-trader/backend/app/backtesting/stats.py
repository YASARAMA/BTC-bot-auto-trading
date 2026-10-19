"""Uncertainty: bootstrap confidence intervals over trades.

Trades are resampled with replacement (seeded, so results reproduce). This assumes trades
are exchangeable, which they are not exactly - consecutive trades share market conditions -
so the intervals are, if anything, too narrow. They are reported as such.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _ci(samples: np.ndarray, alpha: float = 0.05) -> tuple[float, float]:
    return float(np.percentile(samples, 100 * alpha / 2)), float(np.percentile(samples, 100 * (1 - alpha / 2)))


def bootstrap(trades: pd.DataFrame, *, samples: int = 2000, seed: int = 42, alpha: float = 0.05) -> dict[str, Any]:
    n = len(trades)
    if n < 10:
        return {"available": False, "reason": f"only {n} trades; at least 10 are needed for a bootstrap",
                "samples": 0}
    rng = np.random.default_rng(seed)
    r = trades["r_multiple"].fillna(0).to_numpy(dtype=float)
    pct = trades["return_pct"].to_numpy(dtype=float)
    pnl = trades["net_pnl"].to_numpy(dtype=float)
    idx = rng.integers(0, n, size=(samples, n))
    exp_r = r[idx].mean(axis=1)
    exp_pct = pct[idx].mean(axis=1)
    win = (pnl[idx] > 0).mean(axis=1) * 100
    gains = np.where(pnl[idx] > 0, pnl[idx], 0).sum(axis=1)
    losses = -np.where(pnl[idx] <= 0, pnl[idx], 0).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        pf = np.where(losses > 0, gains / losses, np.nan)
    pf = pf[~np.isnan(pf)]
    out = {
        "available": True,
        "samples": samples,
        "seed": seed,
        "confidence": 1 - alpha,
        "expectancy_r": {"point": float(r.mean()), "ci": _ci(exp_r, alpha)},
        "expectancy_pct": {"point": float(pct.mean()), "ci": _ci(exp_pct, alpha)},
        "win_rate_pct": {"point": float((pnl > 0).mean() * 100), "ci": _ci(win, alpha)},
        "profit_factor": {"ci": _ci(pf, alpha) if len(pf) else None},
        "prob_expectancy_below_zero": float((exp_r < 0).mean()),
        "caveat": "trades are not independent; real uncertainty is larger than these intervals suggest",
    }
    return out
