"""Entry filters and the time stop: the two levers that raise the win rate honestly."""
from __future__ import annotations

import pytest

from bot.config import BotConfig, FiltersConfig
from bot.strategy.filters import EntryFilters, higher_timeframe, htf_trend_state
from tests.conftest import TF_MS, T0, make_ohlcv, trending_series


ALIGNED = (T0 // (4 * TF_MS)) * (4 * TF_MS)  # a start that sits on a 4h boundary


def test_higher_timeframe_blocks_are_clock_aligned_and_complete():
    df = make_ohlcv([100 + i for i in range(24)], start_ts=ALIGNED)
    h4 = higher_timeframe(df, 4)
    assert len(h4) == 6
    assert h4["open"].iloc[0] == pytest.approx(df["open"].iloc[0])
    assert h4["close"].iloc[0] == pytest.approx(df["close"].iloc[3])
    assert h4["high"].iloc[0] == pytest.approx(df["high"].iloc[:4].max())
    assert h4["low"].iloc[0] == pytest.approx(df["low"].iloc[:4].min())
    assert h4["volume"].iloc[0] == pytest.approx(df["volume"].iloc[:4].sum())
    # Blocks start where a real 4h candle would, not at whatever row the window begins on.
    assert int(h4["ts"].iloc[0]) % (4 * TF_MS) == 0


def test_partial_blocks_at_both_ends_are_dropped():
    """Reading a block that has not closed is reading the future; counting a first block
    that is only as long as the rolling window happens to start is reading noise."""
    full = higher_timeframe(make_ohlcv([100 + i for i in range(24)], start_ts=ALIGNED), 4)
    partial = higher_timeframe(make_ohlcv([100 + i for i in range(26)], start_ts=ALIGNED), 4)
    assert len(partial) == len(full), "the two extra candles do not complete a block"
    assert partial["close"].iloc[-1] == pytest.approx(full["close"].iloc[-1])

    # A window starting two candles into a 4h block drops that block instead of counting
    # a short one: 24 candles from 22:00 hold five complete 4h blocks, not six.
    ragged = higher_timeframe(make_ohlcv([100 + i for i in range(24)], start_ts=T0), 4)
    assert len(ragged) == 5
    assert int(ragged["ts"].iloc[0]) % (4 * TF_MS) == 0


def test_higher_timeframe_needs_a_sane_factor_and_order():
    df = make_ohlcv([100, 101, 102])
    with pytest.raises(ValueError, match="at least 2"):
        higher_timeframe(df, 1)
    assert higher_timeframe(make_ohlcv([100.0]), 4).empty


def test_trend_state_reports_direction_and_readiness():
    rising = make_ohlcv([100 * 1.004 ** i for i in range(600)])
    falling = make_ohlcv([100 * 0.996 ** i for i in range(600)])
    up = htf_trend_state(rising, 4, 50)
    down = htf_trend_state(falling, 4, 50)
    assert up["ready"] and up["rising"] and up["above"]
    assert down["ready"] and not down["rising"] and not down["above"]
    short = htf_trend_state(make_ohlcv([100 + i for i in range(40)]), 4, 50)
    assert short["ready"] is False and short["needed"] > short["blocks"]


def test_filters_are_off_by_default():
    f = EntryFilters()
    assert f.active is False and f.warmup == 0
    assert f.block_reason(make_ohlcv(trending_series())) is None


def test_the_higher_timeframe_filter_blocks_a_falling_market():
    f = EntryFilters(htf_factor=4, htf_period=50, htf_mode="rising")
    rising = make_ohlcv([100 * 1.004 ** i for i in range(600)])
    falling = make_ohlcv([100 * 0.996 ** i for i in range(600)])
    assert f.block_reason(rising) is None
    assert "trend is falling" in f.block_reason(falling)
    # "both" also wants price above the line, which a pullback inside an uptrend fails.
    # The two modes answer different questions, and a sharp bounce inside a long decline
    # is where they disagree: price is back above the line while the line is still falling.
    closes = [100 * 0.997 ** i for i in range(592)]
    closes += [closes[-1] * 1.12 ** i for i in range(1, 9)]
    bounce = make_ohlcv(closes, start_ts=ALIGNED)
    common = {"htf_factor": 4, "htf_period": 50, "htf_slope_lookback": 5}
    assert EntryFilters(**common, htf_mode="above").block_reason(bounce) is None
    assert "trend is falling" in EntryFilters(**common, htf_mode="rising").block_reason(bounce)
    assert "trend is falling" in EntryFilters(**common, htf_mode="both").block_reason(bounce)

    # And the other way round: still below the line after a decline.
    deep = make_ohlcv([100 * 0.997 ** i for i in range(600)], start_ts=ALIGNED)
    assert "below the 4x timeframe trend line" in EntryFilters(**common, htf_mode="above").block_reason(deep)


def test_a_filter_says_so_when_it_has_not_got_enough_history():
    f = EntryFilters(htf_factor=24, htf_period=50)
    reason = f.block_reason(make_ohlcv([100 + i for i in range(200)]))
    assert "higher timeframe not ready" in reason
    assert f.warmup > 200, "and the warmup makes the requirement visible up front"


def test_the_volatility_window_cuts_both_ends():
    quiet = make_ohlcv([100.0 + (i % 2) * 0.01 for i in range(200)], spread=0.0)
    wild = make_ohlcv([100 * (1.05 if i % 2 else 0.95) ** 1 for i in range(200)], spread=0.02)
    assert "is below" in EntryFilters(min_atr_pct=0.3).block_reason(quiet)
    assert "is above" in EntryFilters(max_atr_pct=1.0).block_reason(wild)
    assert EntryFilters(min_atr_pct=0.001, max_atr_pct=50.0).block_reason(wild) is None


def test_the_clock_filters_use_utc():
    # T0 falls in the 22:00 UTC candle of Tuesday 2023-11-14; the filter looks at the
    # newest candle, so a two-row frame is judged on its 23:00 row.
    night = make_ohlcv([100.0, 101.0], start_ts=T0)
    assert "hour 23:00 UTC is outside" in EntryFilters(hours_utc="6-22").block_reason(night)
    assert EntryFilters(hours_utc="22-3").block_reason(night) is None, "a window across midnight"

    day = make_ohlcv([100.0, 101.0], start_ts=T0 + 9 * TF_MS)  # 08:00 UTC Wednesday
    assert EntryFilters(hours_utc="6-22").block_reason(day) is None

    saturday = make_ohlcv([100.0, 101.0], start_ts=T0 + 4 * 86_400_000)  # Saturday
    assert "weekend" in EntryFilters(skip_weekends=True).block_reason(saturday)
    assert EntryFilters(skip_weekends=True).block_reason(day) is None


def test_hour_windows_parse_including_the_ones_that_wrap_midnight():
    assert sorted(EntryFilters(hours_utc="6-9").allowed_hours()) == [6, 7, 8, 9]
    assert sorted(EntryFilters(hours_utc="22-2").allowed_hours()) == [0, 1, 2, 22, 23]
    assert sorted(EntryFilters(hours_utc="0,12, 23").allowed_hours()) == [0, 12, 23]
    assert EntryFilters(hours_utc="").allowed_hours() is None
    with pytest.raises(ValueError, match="0-23"):
        EntryFilters(hours_utc="25").allowed_hours()


def test_an_unknown_higher_timeframe_mode_is_rejected():
    df = make_ohlcv([100 * 1.004 ** i for i in range(600)])
    with pytest.raises(ValueError, match="htf_mode"):
        EntryFilters(htf_factor=4, htf_mode="sideways").block_reason(df)


def test_the_config_builds_the_filters_it_describes():
    cfg = BotConfig(filters=FiltersConfig(htf_factor=4, htf_period=50, min_atr_pct=0.25, hours_utc="6-22"))
    f = EntryFilters(**cfg.filters.model_dump())
    assert f.active and f.htf_factor == 4 and f.min_atr_pct == 0.25
    assert f.describe()["warmup"] == f.warmup
