"""Deterministic market-regime detector.

Five states, decided by explicit rules in a fixed order of priority:

1. ``uncertain``  - indicators still warming up, a volatility shock beyond the validated
   range, conflicting signals, or confidence below the threshold. No new positions.
2. ``high_volatility_breakout`` - volatility expanding (a candle of at least
   ``expansion_range_atr`` ATR, or ATR in its top percentile band) right after a squeeze,
   with the close outside the recent range.
3. ``trend_up`` - ADX >= ``trend_adx_min``, EMA fast > mid > slow, price above the mid EMA,
   the mid EMA rising by at least ``trend_slope_min_atr`` ATR over ``slope_lookback``
   candles, +DI above -DI, and higher timeframes not pointing down.
4. ``trend_down`` - the mirror image.
5. ``range`` - ADX <= ``range_adx_max``, a flat mid EMA, volatility not expanding and the
   close inside the recent range.

Anything that matches none of these is ``uncertain``. Confidence is a transparent score from
how far each feature clears its threshold and how many higher timeframes agree; below
``min_confidence`` the detector abstains. The detector only reads the feature frame, which
is causal, so it never sees a future candle.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.config import RegimeConfig

TREND_UP = "trend_up"
TREND_DOWN = "trend_down"
RANGE = "range"
BREAKOUT = "high_volatility_breakout"
UNCERTAIN = "uncertain"
REGIMES = (TREND_UP, TREND_DOWN, RANGE, BREAKOUT, UNCERTAIN)

REQUIRED = ["ema_fast", "ema_mid", "ema_slow", "atr", "atr_rank", "adx", "plus_di", "minus_di", "bb_width_rank",
            "slope_mid_atr", "range_high", "range_low", "range_atr", "rel_volume"]


def _clip01(x: pd.Series) -> pd.Series:
    return x.clip(lower=0.0, upper=1.0).fillna(0.0)


def detect(f: pd.DataFrame, cfg: RegimeConfig, confirm_timeframes: list[str]) -> pd.DataFrame:
    """Regime, confidence, direction and a short reason for every row of ``f``."""
    close = f["close"]
    warm = f[REQUIRED].notna().all(axis=1)
    htf = [f[f"h{tf}_trend"] for tf in confirm_timeframes if f"h{tf}_trend" in f]
    htf_known = pd.concat(htf, axis=1).notna().all(axis=1) if htf else pd.Series(True, index=f.index)
    if htf:
        stacked = pd.concat(htf, axis=1)
        up_share = (stacked == 1).sum(axis=1) / len(htf)
        down_share = (stacked == -1).sum(axis=1) / len(htf)
        not_down = (stacked >= 0).sum(axis=1) / len(htf)
        not_up = (stacked <= 0).sum(axis=1) / len(htf)
        all_down = (stacked == -1).all(axis=1)
        all_up = (stacked == 1).all(axis=1)
    else:
        up_share = down_share = pd.Series(0.5, index=f.index)
        not_down = not_up = pd.Series(1.0, index=f.index)
        all_down = all_up = pd.Series(False, index=f.index)

    shock = (f["range_atr"] >= cfg.shock_range_atr) | (f["atr_rank"] >= cfg.shock_percentile)
    expansion = (f["range_atr"] >= cfg.expansion_range_atr) | (f["atr_rank"] >= cfg.expansion_percentile)
    break_up = close > f["range_high"]
    break_down = close < f["range_low"]
    squeeze = f["squeeze_recent"].fillna(False).astype(bool)
    breakout = expansion & squeeze & (break_up | break_down)

    aligned_up = (f["ema_fast"] > f["ema_mid"]) & (f["ema_mid"] > f["ema_slow"]) & (close > f["ema_mid"])
    aligned_down = (f["ema_fast"] < f["ema_mid"]) & (f["ema_mid"] < f["ema_slow"]) & (close < f["ema_mid"])
    strong = f["adx"] >= cfg.trend_adx_min
    trend_up = strong & aligned_up & (f["slope_mid_atr"] >= cfg.trend_slope_min_atr) & (f["plus_di"] > f["minus_di"])
    trend_down = (strong & aligned_down & (f["slope_mid_atr"] <= -cfg.trend_slope_min_atr)
                  & (f["minus_di"] > f["plus_di"]))
    ranging = ((f["adx"] <= cfg.range_adx_max) & (f["slope_mid_atr"].abs() <= cfg.range_slope_max_atr)
               & (f["atr_rank"] < cfg.expansion_percentile) & ~break_up & ~break_down)

    # Confidence for each candidate state, from margins over the thresholds.
    adx_up = _clip01((f["adx"] - cfg.trend_adx_min) / 15.0)
    slope_up = _clip01((f["slope_mid_atr"] - cfg.trend_slope_min_atr) / cfg.trend_slope_min_atr)
    slope_down = _clip01((-f["slope_mid_atr"] - cfg.trend_slope_min_atr) / cfg.trend_slope_min_atr)
    conf_up = 0.45 + 0.15 * adx_up + 0.15 * slope_up + 0.25 * up_share.fillna(0)
    conf_down = 0.45 + 0.15 * adx_up + 0.15 * slope_down + 0.25 * down_share.fillna(0)
    flat_adx = _clip01((cfg.range_adx_max - f["adx"]) / 8.0)
    flat_slope = _clip01(1.0 - f["slope_mid_atr"].abs() / cfg.range_slope_max_atr)
    conf_range = 0.45 + 0.2 * flat_adx + 0.15 * flat_slope + 0.2 * not_down.fillna(0)
    expansion_margin = _clip01((f["range_atr"] - cfg.expansion_range_atr) / cfg.expansion_range_atr)
    volume_support = _clip01((f["rel_volume"] - 1.0) / 1.5)
    with_htf = np.where(break_up, not_down.fillna(0), not_up.fillna(0))
    conf_break = 0.5 + 0.15 * expansion_margin + 0.15 * volume_support + 0.2 * pd.Series(with_htf, index=f.index)

    regime = pd.Series(UNCERTAIN, index=f.index, dtype=object)
    confidence = pd.Series(0.0, index=f.index)
    reason = pd.Series("no rule matched: conditions are mixed", index=f.index, dtype=object)
    direction = pd.Series(0, index=f.index)

    def assign(mask: pd.Series, name: str, conf: pd.Series, why: str, dirn: int | pd.Series = 0) -> None:
        free = mask & (regime == UNCERTAIN) & (reason == "no rule matched: conditions are mixed")
        regime[free] = name
        confidence[free] = conf[free].clip(upper=1.0)
        reason[free] = why
        if isinstance(dirn, pd.Series):
            direction[free] = dirn[free]
        else:
            direction[free] = dirn

    # Priority order: abstentions first, then the specific states.
    reason[~warm] = "indicators warming up"
    reason[warm & ~htf_known] = "higher-timeframe data not available yet"
    blocked = ~warm | ~htf_known
    reason[~blocked & shock] = "volatility shock outside the validated operating range"
    blocked = blocked | shock
    open_ = ~blocked
    assign(open_ & breakout, BREAKOUT, conf_break, "volatility expanding out of a squeeze",
           pd.Series(np.where(break_up, 1, -1), index=f.index))
    conflict_up = open_ & trend_up & all_down
    conflict_down = open_ & trend_down & all_up
    reason[conflict_up | conflict_down] = "trend on the signal timeframe conflicts with every higher timeframe"
    assign(open_ & trend_up & ~all_down, TREND_UP, conf_up, "aligned EMAs, rising mid EMA, ADX above threshold", 1)
    assign(open_ & trend_down & ~all_up, TREND_DOWN, conf_down, "aligned EMAs, falling mid EMA, ADX above threshold", -1)
    assign(open_ & ranging, RANGE, conf_range, "low ADX, flat mid EMA, price inside its recent range")

    low = (regime != UNCERTAIN) & (confidence < cfg.min_confidence)
    reason[low] = ("confidence " + confidence[low].round(2).astype(str) + " below "
                   + str(cfg.min_confidence) + " for " + regime[low].astype(str))
    regime[low] = UNCERTAIN
    return pd.DataFrame({"regime": regime, "regime_confidence": confidence.round(4), "regime_reason": reason,
                         "regime_direction": direction})


def explain(f: pd.DataFrame, i: int, cfg: RegimeConfig, confirm_timeframes: list[str]) -> dict[str, Any]:
    """The features and rule outcomes behind the regime of row ``i``, for the UI and audit trail."""
    row = f.iloc[i]

    def v(name: str, digits: int = 3) -> float | None:
        x = row.get(name)
        return None if x is None or pd.isna(x) else round(float(x), digits)

    return {
        "features": {
            "close": v("close", 8), "ema_fast": v("ema_fast", 8), "ema_mid": v("ema_mid", 8), "ema_slow": v("ema_slow", 8),
            "adx": v("adx", 2), "plus_di": v("plus_di", 2), "minus_di": v("minus_di", 2), "atr_pct": v("atr_pct", 4),
            "atr_rank": v("atr_rank", 1), "bb_width_rank": v("bb_width_rank", 1), "slope_mid_atr": v("slope_mid_atr", 3),
            "range_atr": v("range_atr", 3), "rel_volume": v("rel_volume", 3), "rsi": v("rsi", 2),
            **{f"htf_{tf}": v(f"h{tf}_trend", 0) for tf in confirm_timeframes},
        },
        "thresholds": {
            "trend_adx_min": cfg.trend_adx_min, "range_adx_max": cfg.range_adx_max,
            "trend_slope_min_atr": cfg.trend_slope_min_atr, "range_slope_max_atr": cfg.range_slope_max_atr,
            "squeeze_percentile": cfg.squeeze_percentile, "expansion_percentile": cfg.expansion_percentile,
            "expansion_range_atr": cfg.expansion_range_atr, "shock_range_atr": cfg.shock_range_atr,
            "min_confidence": cfg.min_confidence,
        },
    }
