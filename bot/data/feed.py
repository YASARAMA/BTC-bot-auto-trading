"""CandleFeed: rolling window of CLOSED candles with gap backfill and retries."""
from __future__ import annotations

import logging
from typing import Callable

import pandas as pd

from bot.common import retry_call, timeframe_to_ms
from bot.execution.base import MarketData
from bot.logging_utils import log_event

log = logging.getLogger(__name__)

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def rows_to_frame(rows: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["ts"] = df["ts"].astype("int64")
    for c in COLUMNS[1:]:
        df[c] = df[c].astype("float64")
    return df.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)


class CandleFeed:
    def __init__(
        self,
        market: MarketData,
        *,
        symbol: str,
        timeframe: str,
        window: int,
        max_retries: int = 5,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 60.0,
        fetch_limit: int = 1000,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.market = market
        self.symbol = symbol
        self.timeframe = timeframe
        self.tf_ms = timeframe_to_ms(timeframe)
        self.window = window
        self.max_retries = max_retries
        self.backoff_base = backoff_base_seconds
        self.backoff_max = backoff_max_seconds
        self.fetch_limit = fetch_limit
        self._sleep = sleep
        self.df = pd.DataFrame(columns=COLUMNS)
        self.gaps_detected = 0

    # ----- time helpers ----------------------------------------------------------------
    def last_closed_ts(self, now_ms: int) -> int:
        """Open timestamp of the most recent candle that has fully closed."""
        return (now_ms // self.tf_ms) * self.tf_ms - self.tf_ms

    def next_close_ms(self, now_ms: int) -> int:
        return (now_ms // self.tf_ms + 1) * self.tf_ms

    @property
    def latest_ts(self) -> int | None:
        return int(self.df["ts"].iloc[-1]) if len(self.df) else None

    def candles(self) -> pd.DataFrame:
        return self.df.copy()

    # ----- fetching --------------------------------------------------------------------
    def _fetch(self, since: int | None, limit: int) -> pd.DataFrame:
        kwargs = {}
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        rows = retry_call(
            lambda: self.market.fetch_ohlcv(self.symbol, self.timeframe, since, limit),
            retries=self.max_retries, base_seconds=self.backoff_base, max_seconds=self.backoff_max,
            logger=log, what=f"fetch_ohlcv {self.symbol} {self.timeframe}", **kwargs,
        )
        return rows_to_frame(rows) if rows else pd.DataFrame(columns=COLUMNS)

    def refresh(self, now_ms: int) -> pd.DataFrame:
        """Fetch what is needed to bring the window up to the last closed candle.

        Returns the newly added closed candles (possibly empty)."""
        cutoff = self.last_closed_ts(now_ms)
        if self.latest_ts is None:
            since = cutoff - (self.window + 1) * self.tf_ms
        else:
            # Re-fetch from a few candles back so a corrected candle is picked up.
            since = self.latest_ts - 2 * self.tf_ms
        fetched = self._fetch(since, min(self.fetch_limit, self.window + 5))
        closed = fetched[fetched["ts"] <= cutoff]
        before = set(self.df["ts"].tolist())
        merged = pd.concat([self.df, closed], ignore_index=True) if len(self.df) else closed
        merged = merged.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
        merged = self._backfill_gaps(merged, cutoff)
        self.df = merged.tail(self.window).reset_index(drop=True)
        new = self.df[~self.df["ts"].isin(before)]
        return new.reset_index(drop=True)

    def _backfill_gaps(self, df: pd.DataFrame, cutoff: int) -> pd.DataFrame:
        """Fill missing candles inside the window by re-fetching the gap range."""
        if len(df) < 2:
            return df
        for _ in range(5):  # bounded: never loop forever on a venue that truly has holes
            ts = df["ts"].to_numpy()
            diffs = ts[1:] - ts[:-1]
            idx = [i for i, d in enumerate(diffs) if d != self.tf_ms]
            if not idx:
                return df
            i = idx[0]
            start, end = int(ts[i]), int(ts[i + 1])
            self.gaps_detected += 1
            log_event(log, "candle_gap", level=logging.WARNING, gap_start=start, gap_end=end,
                      missing=int((end - start) // self.tf_ms - 1))
            fill = self._fetch(start + self.tf_ms, min(self.fetch_limit, int((end - start) // self.tf_ms)))
            fill = fill[(fill["ts"] > start) & (fill["ts"] < end) & (fill["ts"] <= cutoff)]
            if fill.empty:
                # The exchange has no data for that range (maintenance). Accept the hole.
                log_event(log, "candle_gap_unfillable", level=logging.WARNING, gap_start=start, gap_end=end)
                # Drop everything before the hole so indicators never straddle it.
                return df.iloc[i + 1 :].reset_index(drop=True)
            df = pd.concat([df, fill], ignore_index=True).drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
        return df
