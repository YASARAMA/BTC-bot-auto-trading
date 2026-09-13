import pytest

from bot.backtest.optimize import (
    DEFAULT_GRID,
    OBJECTIVES,
    Candidate,
    expand_grid,
    grid_search,
    walk_forward,
)
from bot.data.history import load_csv
from tests.conftest import make_ohlcv
from tests.test_backtest import wavy


SMALL_GRID = {"ema_fast": [8, 12], "ema_slow": [26, 50], "atr_stop_mult": [1.5, 3.0]}


def test_expand_grid_drops_impossible_combinations():
    combos = expand_grid({"ema_fast": [8, 30], "ema_slow": [26], "rsi_buy_min": [40.0, 80.0], "rsi_buy_max": [70.0]})
    assert {"ema_fast": 30, "ema_slow": 26, "rsi_buy_min": 40.0, "rsi_buy_max": 70.0} not in combos
    assert all(c["ema_fast"] < c["ema_slow"] and c["rsi_buy_min"] < c["rsi_buy_max"] for c in combos)
    assert len(expand_grid(DEFAULT_GRID, sample=25)) == 25
    assert expand_grid(DEFAULT_GRID, sample=9, seed=1) == expand_grid(DEFAULT_GRID, sample=9, seed=1)


def test_grid_search_ranks_and_reports_every_combination(cfg):
    df = make_ohlcv(wavy(1200), spread=0.002)
    ranked = grid_search(df, cfg, grid=SMALL_GRID, min_trades=1, workers=1)
    assert len(ranked) == len(expand_grid(SMALL_GRID))
    assert all(isinstance(c, Candidate) for c in ranked)
    scores = [c.score for c in ranked]
    assert scores == sorted(scores, reverse=True), "best first"
    best = ranked[0]
    assert set(best.params) == set(SMALL_GRID)
    assert best.metrics["trades"] >= 1 and "total_return_pct" in best.metrics
    assert best.to_dict()["metrics"]["max_drawdown_pct"] is not None


def test_min_trades_rejects_thin_results(cfg):
    df = make_ohlcv(wavy(1200), spread=0.002)
    ranked = grid_search(df, cfg, grid=SMALL_GRID, min_trades=10_000, workers=1)
    assert all(c.score == float("-inf") for c in ranked), "nothing can pass an impossible trade count"


def test_objectives_disagree_in_the_expected_direction():
    high_return = {"total_return_pct": 60.0, "max_drawdown_pct": -50.0, "sharpe": 0.4, "profit_factor": 1.1, "trades": 30}
    steady = {"total_return_pct": 20.0, "max_drawdown_pct": -5.0, "sharpe": 1.8, "profit_factor": 1.9, "trades": 30}
    assert OBJECTIVES["return"](high_return) > OBJECTIVES["return"](steady)
    assert OBJECTIVES["return_over_drawdown"](steady) > OBJECTIVES["return_over_drawdown"](high_return)
    assert OBJECTIVES["sharpe"](steady) > OBJECTIVES["sharpe"](high_return)


def test_walk_forward_tests_on_unseen_blocks(cfg):
    df = make_ohlcv(wavy(4000), spread=0.002)
    out = walk_forward(df, cfg, grid=SMALL_GRID, folds=2, min_trades=1, workers=1)
    assert len(out["folds"]) == 2
    for fold in out["folds"]:
        assert fold["train"]["to"] <= fold["test"]["to"], "the test block never ends before the training block"
        assert set(fold["chosen"]) == set(SMALL_GRID)
        assert "total_return_pct" in fold["test_metrics"]
    assert out["out_of_sample"]["trades"] >= 0
    assert out["stability"]["folds"] == 2
    assert out["equity"] and out["verdict"]
    assert any(word in out["verdict"] for word in ("out of sample", "lost", "blocks"))


def test_walk_forward_refuses_a_history_that_is_too_short(cfg):
    df = make_ohlcv(wavy(300), spread=0.002)
    with pytest.raises(ValueError, match="not enough candles"):
        walk_forward(df, cfg, grid=SMALL_GRID, folds=6, workers=1)


def test_verdict_calls_out_a_losing_strategy(cfg):
    from bot.backtest.optimize import _verdict

    losing = {"total_return_pct": -12.0, "buy_hold_return_pct": 40.0}
    assert "do not trade it" in _verdict(losing, {"profitable_folds": 1, "folds": 4,
                                                  "avg_in_sample_return_pct": 9.0,
                                                  "avg_out_of_sample_return_pct": -3.0}, []).lower()
    lucky = {"total_return_pct": 5.0, "buy_hold_return_pct": 2.0}
    assert "lucky stretch" in _verdict(lucky, {"profitable_folds": 1, "folds": 4,
                                               "avg_in_sample_return_pct": 9.0,
                                               "avg_out_of_sample_return_pct": 1.0}, [])
    beaten = {"total_return_pct": 5.0, "buy_hold_return_pct": 90.0}
    assert "buying and holding" in _verdict(beaten, {"profitable_folds": 3, "folds": 4,
                                                     "avg_in_sample_return_pct": 9.0,
                                                     "avg_out_of_sample_return_pct": 2.0}, [])


def test_real_data_walk_forward_is_honest_about_the_reference_strategy(cfg):
    """The shipped strategy must not be presented as a money printer."""
    df = load_csv("data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv")
    out = walk_forward(df, cfg, grid=SMALL_GRID, folds=2, min_trades=2, workers=2)
    oos = out["out_of_sample"]["total_return_pct"]
    hold = out["out_of_sample"]["buy_hold_return_pct"]
    assert hold > oos, "buy and hold beat the strategy on this period, and the tool should say so"
    assert "buying and holding" in out["verdict"] or "do not trade" in out["verdict"].lower()
