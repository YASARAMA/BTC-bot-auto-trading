"""CsvMarketData: serves candles and a ticker from a CSV file with a simulated clock.

Lets the full main loop run without network access (development, CI, demos). The
clock advances by one candle each time advance() is called; the "ticker" is the close
of the most recent candle whose close time is at or before the clock.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from bot.common import timeframe_to_ms
from bot.execution.base import MarketData, MarketInfo


class CsvMarketData(MarketData):
    def __init__(self, df: pd.DataFrame, timeframe: str, start_index: int | None = None,
                 clock_offset_ms: int = 10_000) -> None:
        from bot.strategy.base import validate_frame

        validate_frame(df)
        self.df = df.sort_values("ts").reset_index(drop=True)
        self.timeframe = timeframe
        self.tf_ms = timeframe_to_ms(timeframe)
        self.index = start_index if start_index is not None else 0
        self.clock_offset_ms = clock_offset_ms  # how long after a close the bot 'sees' it
        self._info = MarketInfo(amount_step=1e-6, min_amount=1e-5, min_notional=5.0)

    @classmethod
    def from_csv(cls, path: str | Path, timeframe: str, start_index: int | None = None,
                 clock_offset_ms: int = 10_000) -> "CsvMarketData":
        from bot.data.history import load_csv

        return cls(load_csv(path), timeframe, start_index, clock_offset_ms)

    @property
    def exhausted(self) -> bool:
        return self.index >= len(self.df) - 1

    def advance(self, candles: int = 1) -> None:
        self.index = min(len(self.df) - 1, self.index + candles)

    def now_ms(self) -> int:
        # Clock sits shortly after the close of candle `index`.
        return int(self.df["ts"].iloc[self.index]) + self.tf_ms + self.clock_offset_ms

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None) -> list[list[float]]:
        if timeframe != self.timeframe:
            raise ValueError(f"replay data is {self.timeframe}, requested {timeframe}")
        end = self.index + 2  # include the "open" candle after the clock like a real exchange would
        frame = self.df.iloc[:end]
        if since is not None:
            frame = frame[frame["ts"] >= since]
        if limit is not None:
            frame = frame.iloc[:limit]
        return frame[["ts", "open", "high", "low", "close", "volume"]].values.tolist()

    def fetch_ticker_price(self, symbol: str) -> float:
        return float(self.df["close"].iloc[self.index])

    def market_info(self, symbol: str) -> MarketInfo:
        return self._info
