from __future__ import annotations

import math

import pandas as pd

from app.data.validation import find_gaps, frame_from_rows, validate_candles
from tests.fakes import synthetic_candles

M15 = 900_000


def test_clean_series_passes_untouched() -> None:
    df = synthetic_candles(100)
    clean, rep = validate_candles(df, "15m")
    assert rep.ok and rep.rows_out == 100 and rep.missing_bars == 0
    pd.testing.assert_frame_equal(clean[["ts", "close"]], df[["ts", "close"]])


def test_bad_rows_are_removed_and_counted_never_repaired() -> None:
    df = synthetic_candles(10)
    df.loc[1, "close"] = float("nan")
    df.loc[2, "open"] = -5
    df.loc[3, "high"] = df.loc[3, "low"] - 1          # high below low
    df.loc[4, "volume"] = -1
    df.loc[5, "ts"] = df.loc[5, "ts"] + 1             # not on a 15m boundary
    clean, rep = validate_candles(df, "15m")
    assert rep.non_finite == 1 and rep.non_positive_price == 1 and rep.inconsistent_ohlc == 1
    assert rep.negative_volume == 1 and rep.misaligned == 1
    assert rep.rows_out == 5 and not rep.ok
    # The removed rows are now gaps; nothing was interpolated into them.
    assert rep.missing_bars == 5
    assert not clean["close"].isna().any()


def test_duplicates_and_order() -> None:
    df = synthetic_candles(6)
    shuffled = pd.concat([df.iloc[[3, 0, 2]], df.iloc[[1, 4, 5]], df.iloc[[2]]])
    clean, rep = validate_candles(shuffled, "15m")
    assert rep.unsorted and rep.duplicates == 1 and rep.conflicting_duplicates == 0
    assert clean["ts"].tolist() == df["ts"].tolist()


def test_conflicting_duplicates_are_reported() -> None:
    df = synthetic_candles(3)
    dup = df.iloc[[1]].copy()
    dup["close"] = dup["close"] * 1.01
    dup["high"] = dup[["high", "close"]].max(axis=1)
    _, rep = validate_candles(pd.concat([df, dup]), "15m")
    assert rep.conflicting_duplicates == 1 and not rep.ok


def test_forming_and_future_candles_are_not_kept() -> None:
    df = synthetic_candles(5)
    last_open = int(df["ts"].iloc[-1])
    now = last_open + M15 // 2                        # the last candle is still forming
    clean, rep = validate_candles(df, "15m", now_ms=now)
    assert rep.forming_dropped == 1 and clean["ts"].iloc[-1] == last_open - M15
    future = pd.concat([df, df.iloc[[-1]].assign(ts=last_open + 10 * M15)])
    _, rep2 = validate_candles(future, "15m", now_ms=last_open + 2 * M15)
    assert rep2.future_dropped == 1


def test_gaps_listed() -> None:
    df = synthetic_candles(20).drop(index=[5, 6, 12]).reset_index(drop=True)
    _, rep = validate_candles(df, "15m")
    assert rep.missing_bars == 3 and len(rep.gaps) == 2
    first_gap = rep.gaps[0]
    assert first_gap[1] - first_gap[0] == M15
    assert any("not filled" in i for i in rep.issues())


def test_find_gaps_math() -> None:
    import numpy as np

    missing, gaps = find_gaps(np.array([0, 1, 2, 5, 6, 10]) * M15, M15)
    assert missing == 5
    assert gaps == [(3 * M15, 4 * M15), (7 * M15, 9 * M15)]


def test_extreme_moves_flagged_not_dropped() -> None:
    df = synthetic_candles(10)
    df.loc[5, ["open", "high", "low", "close"]] = [df.loc[4, "close"], df.loc[4, "close"] * 1.6,
                                                   df.loc[4, "close"], df.loc[4, "close"] * 1.5]
    df.loc[6, "open"] = df.loc[5, "close"]
    df.loc[6, "high"] = max(df.loc[6, "high"], df.loc[6, "open"], df.loc[6, "close"])
    df.loc[6, "low"] = min(df.loc[6, "low"], df.loc[6, "open"], df.loc[6, "close"])
    clean, rep = validate_candles(df, "15m")
    assert rep.extreme_moves >= 1 and rep.rows_out == 10


def test_frame_from_ccxt_rows() -> None:
    df = frame_from_rows([[0, "1", "2", "0.5", "1.5", "10", "extra"], [M15, 1.5, 2, 1, 1.8, 3]])
    assert df["close"].tolist() == [1.5, 1.8] and df["ts"].tolist() == [0, M15]
    assert not math.isnan(df["volume"].iloc[0])
