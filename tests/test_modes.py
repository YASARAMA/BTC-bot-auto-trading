import pytest

from bot.config import BotConfig, load_config
from bot.modes import MODES, apply_mode, detect_mode, mode_list


def test_every_mode_produces_a_valid_config():
    base = load_config("config.yaml").printable()
    for name in MODES:
        out = apply_mode(base, name)
        cfg = BotConfig.model_validate(out)
        assert cfg.mode == name
        assert detect_mode(out) == name


def test_modes_are_ordered_by_risk():
    risk = {k: v["risk"] for k, v in MODES.items()}
    assert risk["safe"]["risk_per_trade_pct"] < risk["balanced"]["risk_per_trade_pct"] < risk["aggressive"]["risk_per_trade_pct"]
    assert risk["safe"]["max_position_pct"] < risk["balanced"]["max_position_pct"] < risk["aggressive"]["max_position_pct"]
    assert risk["safe"]["max_daily_loss_pct"] < risk["balanced"]["max_daily_loss_pct"] < risk["aggressive"]["max_daily_loss_pct"]
    assert risk["safe"]["min_confidence"] > risk["balanced"]["min_confidence"] > risk["aggressive"]["min_confidence"]


def test_apply_mode_keeps_untouched_settings_and_edits_ai_params():
    base = load_config("config.yaml").printable()
    base["exchange"]["symbol"] = "ETH/USDT"
    base["paper"]["initial_cash"] = 4242.0
    out = apply_mode(base, "safe")
    assert out["exchange"]["symbol"] == "ETH/USDT" and out["paper"]["initial_cash"] == 4242.0
    assert out["strategy"]["params"]["atr_stop_mult"] == 3.0
    ai_cfg = apply_mode({**base, "strategy": {"name": "ai", "params": {"model": "claude-sonnet-5"}}}, "aggressive")
    p = ai_cfg["strategy"]["params"]
    assert p["model"] == "claude-sonnet-5" and p["mode"] == "aggressive" and p["max_stop_atr"] == 2.0
    assert p["indicator_params"]["ema_fast"] == 8
    BotConfig.model_validate(ai_cfg)


def test_hand_edited_config_detects_as_no_mode():
    base = apply_mode(load_config("config.yaml").printable(), "balanced")
    base["risk"]["risk_per_trade_pct"] = 1.7
    assert detect_mode(base) is None
    assert [m["id"] for m in mode_list()] == ["safe", "balanced", "aggressive"]
    with pytest.raises(ValueError):
        apply_mode(base, "yolo")


def test_every_mode_fits_every_strategy():
    """A mode must never push one strategy's parameter names into another: the models
    reject unknown keys, so that would stop the bot at startup."""
    from bot.config import load_config
    from bot.strategy import STRATEGIES, get_strategy

    base = load_config("config.yaml").printable()
    for name in STRATEGIES:
        for mode in MODES:
            out = apply_mode({**base, "strategy": {"name": name, "params": {}}}, mode)
            cfg = BotConfig.model_validate(out)
            strategy = get_strategy(name, cfg.strategy.params)
            assert strategy.warmup <= cfg.exchange.candle_history, (
                f"{name} in {mode} mode needs more history than the default window")


def test_mode_keeps_parameters_it_does_not_define():
    from bot.config import load_config
    from bot.modes import mode_params_for

    base = load_config("config.yaml").printable()
    out = apply_mode({**base, "strategy": {"name": "breakout", "params": {"warmup_factor": 4}}}, "safe")
    params = out["strategy"]["params"]
    assert params["warmup_factor"] == 4, "a setting the mode says nothing about must survive"
    assert params["entry_period"] == mode_params_for("safe", "breakout")["entry_period"]
    # Nested groups merge rather than replace wholesale.
    nested = apply_mode({**base, "strategy": {"name": "regime",
                                              "params": {"trend_params": {"warmup_factor": 3}}}}, "balanced")
    trend = nested["strategy"]["params"]["trend_params"]
    assert trend["warmup_factor"] == 3 and trend["entry_period"] == 20


def test_modes_carry_the_entry_filters_and_the_history_they_need():
    """A mode that switches on a daily filter has to raise the candle window with it, or
    the bot refuses to start and the mode is unusable."""
    from bot.config import load_config
    from bot.strategy.filters import EntryFilters

    base = load_config("config.yaml").printable()
    for mode in MODES:
        cfg = BotConfig.model_validate(apply_mode(base, mode))
        filters = EntryFilters(**cfg.filters.model_dump())
        assert filters.warmup <= cfg.exchange.candle_history, f"{mode} cannot feed its own filters"
    safe = BotConfig.model_validate(apply_mode(base, "safe"))
    aggressive = BotConfig.model_validate(apply_mode(base, "aggressive"))
    assert safe.filters.htf_factor and not aggressive.filters.htf_factor, "safe filters more than aggressive"
    assert safe.filters.skip_weekends and aggressive.filters.skip_weekends
    # Both ends use a time stop, for opposite reasons: safe is patient with it, aggressive
    # wants the capital back quickly. Balanced leaves it off - it helped one sample file
    # and not the other, which is not enough to make it a default.
    balanced = BotConfig.model_validate(apply_mode(base, "balanced"))
    assert safe.exits.time_stop_candles > aggressive.exits.time_stop_candles > 0
    assert balanced.exits.time_stop_candles == 0
