"""Candle validation.

The rule throughout: report everything, invent nothing. A malformed row is removed and
counted; a missing candle stays missing and is listed as a gap. Nothing here interpolates,
forward-fills or otherwise produces a price that the exchange did not print.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from app.core.timeframes import tf_ms

CANDLE_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close"]


@dataclass
class ValidationReport:
    timeframe: str
    rows_in: int = 0
    rows_out: int = 0
    duplicates: int = 0
    conflicting_duplicates: int = 0
    unsorted: bool = False
    misaligned: int = 0
    non_finite: int = 0
    non_positive_price: int = 0
    inconsistent_ohlc: int = 0
    negative_volume: int = 0
    zero_volume: int = 0
    forming_dropped: int = 0
    future_dropped: int = 0
    missing_bars: int = 0
    gaps: list[tuple[int, int]] = field(default_factory=list)
    extreme_moves: int = 0
    first_ts: int | None = None
    last_ts: int | None = None

    @property
    def dropped_invalid(self) -> int:
        return (self.misaligned + self.non_finite + self.non_positive_price + self.inconsistent_ohlc
                + self.negative_volume + self.future_dropped)

    @property
    def ok(self) -> bool:
        """True when nothing had to be removed for being wrong. Gaps alone do not fail it."""
        return self.dropped_invalid == 0 and self.conflicting_duplicates == 0

    def issues(self) -> list[str]:
        out: list[str] = []
        if self.unsorted:
            out.append("rows were out of order and have been sorted")
        if self.duplicates:
            out.append(f"{self.duplicates} duplicate timestamps (kept the last copy)")
        if self.conflicting_duplicates:
            out.append(f"{self.conflicting_duplicates} duplicates disagreed on prices")
        if self.misaligned:
            out.append(f"{self.misaligned} timestamps not on a {self.timeframe} boundary were removed")
        if self.non_finite:
            out.append(f"{self.non_finite} rows with missing or non-numeric values were removed")
        if self.non_positive_price:
            out.append(f"{self.non_positive_price} rows with a zero or negative price were removed")
        if self.inconsistent_ohlc:
            out.append(f"{self.inconsistent_ohlc} rows whose high/low did not contain open/close were removed")
        if self.negative_volume:
            out.append(f"{self.negative_volume} rows with negative volume were removed")
        if self.future_dropped:
            out.append(f"{self.future_dropped} rows dated in the future were removed")
        if self.forming_dropped:
            out.append(f"{self.forming_dropped} candle(s) still forming were not stored as complete")
        if self.missing_bars:
            out.append(f"{self.missing_bars} candles missing in {len(self.gaps)} gap(s); they were not filled")
        if self.zero_volume:
            out.append(f"{self.zero_volume} candles with zero volume (kept; often exchange maintenance)")
        if self.extreme_moves:
            out.append(f"{self.extreme_moves} candles moved more than 30% from the previous close (kept, flagged)")
        return out

    def to_dict(self, max_gaps: int = 50) -> dict[str, Any]:
        d = asdict(self)
        d["gaps"] = [list(g) for g in self.gaps[:max_gaps]]
        d["gaps_total"] = len(self.gaps)
        d["dropped_invalid"] = self.dropped_invalid
        d["ok"] = self.ok
        d["issues"] = self.issues()
        return d


def frame_from_rows(rows: Iterable[Sequence[Any]]) -> pd.DataFrame:
    """Turn ``[[ts, o, h, l, c, v], ...]`` (the CCXT shape) into a candle frame."""
    data = [list(r[:6]) for r in rows]
    df = pd.DataFrame(data, columns=CANDLE_COLUMNS)
    return coerce(df)


def coerce(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in PRICE_COLUMNS + ["volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    out["ts"] = pd.to_numeric(out["ts"], errors="coerce")
    return out


def find_gaps(ts: np.ndarray, step: int) -> tuple[int, list[tuple[int, int]]]:
    """Missing candle count and ``(first_missing, last_missing)`` open times of each gap."""
    if len(ts) < 2:
        return 0, []
    diffs = np.diff(ts)
    idx = np.nonzero(diffs > step)[0]
    gaps = [(int(ts[i] + step), int(ts[i + 1] - step)) for i in idx]
    missing = int(((diffs[idx] // step) - 1).sum()) if len(idx) else 0
    return missing, gaps


def validate_candles(df: pd.DataFrame, timeframe: str, *, now_ms: int | None = None,
                     drop_forming: bool = True, extreme_move_pct: float = 30.0) -> tuple[pd.DataFrame, ValidationReport]:
    step = tf_ms(timeframe)
    report = ValidationReport(timeframe=timeframe, rows_in=len(df))
    if df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS), report

    work = coerce(df[CANDLE_COLUMNS + [c for c in df.columns if c not in CANDLE_COLUMNS]])

    finite = np.isfinite(work[PRICE_COLUMNS + ["volume", "ts"]].to_numpy(dtype="float64")).all(axis=1)
    report.non_finite = int((~finite).sum())
    work = work[finite].copy()
    work["ts"] = work["ts"].astype("int64")

    aligned = (work["ts"] % step) == 0
    report.misaligned = int((~aligned).sum())
    work = work[aligned]

    positive = (work[PRICE_COLUMNS] > 0).all(axis=1)
    report.non_positive_price = int((~positive).sum())
    work = work[positive]

    hi_ok = work["high"] >= work[["open", "close", "low"]].max(axis=1)
    lo_ok = work["low"] <= work[["open", "close", "high"]].min(axis=1)
    consistent = hi_ok & lo_ok
    report.inconsistent_ohlc = int((~consistent).sum())
    work = work[consistent]

    vol_ok = work["volume"] >= 0
    report.negative_volume = int((~vol_ok).sum())
    work = work[vol_ok]

    if not work["ts"].is_monotonic_increasing:
        report.unsorted = True
    work = work.sort_values("ts", kind="mergesort")

    dup_mask = work["ts"].duplicated(keep="last")
    report.duplicates = int(dup_mask.sum())
    if report.duplicates:
        dups = work[work["ts"].duplicated(keep=False)]
        disagree = dups.groupby("ts")[PRICE_COLUMNS].nunique().gt(1).any(axis=1)
        report.conflicting_duplicates = int(disagree.sum())
    work = work[~dup_mask]

    if now_ms is not None:
        future = work["ts"] > now_ms
        report.future_dropped = int(future.sum())
        work = work[~future]
        if drop_forming:
            forming = (work["ts"] + step) > now_ms
            report.forming_dropped = int(forming.sum())
            work = work[~forming]

    work = work.reset_index(drop=True)
    report.zero_volume = int((work["volume"] == 0).sum())
    if len(work) > 1:
        moves = (work["close"] / work["close"].shift(1) - 1).abs() * 100
        report.extreme_moves = int((moves > extreme_move_pct).sum())
    ts = work["ts"].to_numpy(dtype="int64")
    report.missing_bars, report.gaps = find_gaps(ts, step)
    report.rows_out = len(work)
    if len(work):
        report.first_ts, report.last_ts = int(ts[0]), int(ts[-1])
    return work, report
