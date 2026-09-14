"""Entry filters: when *not* to take a signal.

Every strategy here answers "is this a setup?". These filters answer the other question,
"is this a market worth taking a setup in?", and they apply to whichever strategy is
selected, the AI one included. They only ever block an entry - an exit is never filtered,
because a position that needs to close has to close.

Measured on the two sample files, entry quality is the only lever that raises the win rate
without taking the money back out somewhere else: tightening the target raises the hit rate
and loses money, while filtering out the worst entries raises the hit rate, raises the
profit per trade and halves the drawdown.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from bot.strategy.indicators import atr, ema


def higher_timeframe(df: pd.DataFrame, factor: int) -> pd.DataFrame:
    """Aggregate candles into blocks `factor` times longer, aligned to the clock.

    Grouping by timestamp rather than by row keeps the blocks where a real 4h or daily
    candle would be, so the trend line does not shift with wherever the window happens to
    start. Partial blocks at both ends are dropped: the last one has not closed yet, and a
    filter must not read a candle that is still forming, while the first one is only as
    long as the rolling window happened to begin - counting it would make the oldest value
    in the trend line depend on when the bot was started.
    """
    if factor < 2:
        raise ValueError("factor must be at least 2")
    if len(df) < 2:
        return df.iloc[0:0]
    step = int(df["ts"].iloc[1]) - int(df["ts"].iloc[0])
    if step <= 0:
        raise ValueError("candles must be in ascending time order")
    block = step * factor
    bucket = (df["ts"] // block) * block
    grouped = df.groupby(bucket).agg(
        ts=("ts", "first"), open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    ).reset_index(drop=True)
    counts = df.groupby(bucket).size().reset_index(drop=True)
    start = 1 if len(grouped) and counts.iloc[0] < factor else 0
    end = len(grouped) - (1 if len(grouped) > start and counts.iloc[-1] < factor else 0)
    return grouped.iloc[start:end].reset_index(drop=True)


def htf_trend_state(df: pd.DataFrame, factor: int, period: int, slope_lookback: int = 1) -> dict[str, Any]:
    """Where the higher timeframe stands: its trend line, and whether it is rising."""
    htf = higher_timeframe(df, factor)
    line = ema(htf["close"], period)
    if len(htf) < period + slope_lookback:
        return {"ready": False, "blocks": len(htf), "needed": period + slope_lookback}
    close = float(htf["close"].iloc[-1])
    now, earlier = float(line.iloc[-1]), float(line.iloc[-1 - slope_lookback])
    return {"ready": True, "close": close, "ema": now, "above": close > now, "rising": now > earlier,
            "blocks": len(htf)}


@dataclass(frozen=True)
class EntryFilters:
    """Built from the `filters` section of the configuration. All off by default."""

    htf_factor: int = 0
    htf_period: int = 50
    htf_mode: str = "rising"          # rising | above | both
    htf_slope_lookback: int = 3
    min_atr_pct: float = 0.0
    max_atr_pct: float = 0.0
    atr_period: int = 14
    hours_utc: str = ""               # "" = every hour, "6-22" = a window, "0,1,2" = a list
    skip_weekends: bool = False

    @property
    def active(self) -> bool:
        return bool(self.htf_factor or self.min_atr_pct or self.max_atr_pct or self.hours_utc
                    or self.skip_weekends)

    @property
    def warmup(self) -> int:
        """Extra candles the filters themselves need before they can answer."""
        htf = int(self.htf_factor * (self.htf_period + self.htf_slope_lookback) * 1.3) if self.htf_factor else 0
        vol = self.atr_period * 3 if (self.min_atr_pct or self.max_atr_pct) else 0
        return max(htf, vol)

    def allowed_hours(self) -> set[int] | None:
        text = self.hours_utc.strip()
        if not text:
            return None
        hours: set[int] = set()
        for part in text.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                start, end = part.split("-", 1)
                a, b = int(start), int(end)
                hours.update(range(a, b + 1) if a <= b else list(range(a, 24)) + list(range(0, b + 1)))
            else:
                hours.add(int(part))
        bad = [h for h in hours if not 0 <= h <= 23]
        if bad:
            raise ValueError(f"hours must be within 0-23, got {sorted(bad)}")
        return hours or None

    def block_reason(self, df: pd.DataFrame) -> str | None:
        """Why this candle is not a moment to enter, or None when it is fine."""
        if not self.active or df.empty:
            return None
        when = datetime.fromtimestamp(int(df["ts"].iloc[-1]) / 1000, tz=timezone.utc)

        if self.skip_weekends and when.weekday() >= 5:
            return f"weekend ({when:%a}); thin liquidity moves price without meaning it"
        hours = self.allowed_hours()
        if hours is not None and when.hour not in hours:
            return f"hour {when.hour:02d}:00 UTC is outside the trading window {self.hours_utc}"

        if self.min_atr_pct or self.max_atr_pct:
            value = atr(df, self.atr_period)
            if value.isna().iloc[-1]:
                return f"volatility filter not ready ({len(df)} candles)"
            pct = float(value.iloc[-1]) / float(df["close"].iloc[-1]) * 100.0
            if self.min_atr_pct and pct < self.min_atr_pct:
                return f"volatility {pct:.2f}% is below {self.min_atr_pct:g}%: the market is not moving"
            if self.max_atr_pct and pct > self.max_atr_pct:
                return f"volatility {pct:.2f}% is above {self.max_atr_pct:g}%: too wild to size a stop on"

        if self.htf_factor:
            if self.htf_mode not in ("rising", "above", "both"):
                raise ValueError("htf_mode must be rising, above or both")
            state = htf_trend_state(df, self.htf_factor, self.htf_period, self.htf_slope_lookback)
            if not state["ready"]:
                return (f"higher timeframe not ready ({state['blocks']}/{state['needed']} "
                        f"{self.htf_factor}x blocks)")
            label = f"{self.htf_factor}x timeframe"
            if self.htf_mode in ("rising", "both") and not state["rising"]:
                return f"{label} trend is falling"
            if self.htf_mode in ("above", "both") and not state["above"]:
                return f"price is below the {label} trend line"
        return None

    def describe(self) -> dict[str, Any]:
        return {"active": self.active, "htf_factor": self.htf_factor, "htf_period": self.htf_period,
                "htf_mode": self.htf_mode, "min_atr_pct": self.min_atr_pct, "max_atr_pct": self.max_atr_pct,
                "hours_utc": self.hours_utc, "skip_weekends": self.skip_weekends, "warmup": self.warmup}
