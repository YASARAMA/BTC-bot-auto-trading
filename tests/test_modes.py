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
