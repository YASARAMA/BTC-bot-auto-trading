import pytest

from bot.config import BotConfig, LiveSafetyError, Secrets, load_config, load_secrets, resolve_mode


def test_load_config_defaults_and_overrides(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("exchange:\n  symbol: ETH/USDT\nrisk:\n  risk_per_trade_pct: 2\n")
    cfg = load_config(p)
    assert cfg.exchange.symbol == "ETH/USDT" and cfg.exchange.base == "ETH" and cfg.exchange.quote == "USDT"
    assert cfg.risk.risk_per_trade_pct == 2.0 and cfg.risk.max_position_pct == 25.0
    assert cfg.strategy.name == "breakout"


def test_unknown_keys_and_bad_values_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("risk:\n  max_leverage: 5\n")
    with pytest.raises(ValueError):
        load_config(p)
    p.write_text("exchange:\n  timeframe: 7x\n")
    with pytest.raises(ValueError):
        load_config(p)


def test_repo_config_loads():
    cfg = load_config("config.yaml")
    assert cfg.exchange.live is False
    assert cfg.exchange.timeframe == "1h" and cfg.exchange.symbol == "BTC/USDT"
    assert cfg.risk.risk_per_trade_pct == 1.0 and cfg.risk.max_daily_loss_pct == 3.0 and cfg.risk.max_position_pct == 25.0


def test_paper_is_default():
    assert resolve_mode(BotConfig(), Secrets(), env={}) == "paper"
    assert resolve_mode(BotConfig(), Secrets(), env={"LIVE_TRADING": "false"}) == "paper"


def test_live_requires_both_switches_and_keys():
    live_cfg = BotConfig.model_validate({"exchange": {"live": True}})
    keys = Secrets(api_key="k", api_secret="s")
    with pytest.raises(LiveSafetyError):
        resolve_mode(BotConfig(), keys, env={"LIVE_TRADING": "true"})  # env only
    with pytest.raises(LiveSafetyError):
        resolve_mode(live_cfg, keys, env={})  # config only
    with pytest.raises(LiveSafetyError):
        resolve_mode(live_cfg, Secrets(), env={"LIVE_TRADING": "true"})  # both, but no keys
    assert resolve_mode(live_cfg, keys, env={"LIVE_TRADING": "true"}) == "live"


def test_secrets_from_env_and_redaction():
    s = load_secrets({"EXCHANGE_API_KEY": " abc ", "EXCHANGE_API_SECRET": "", "TELEGRAM_BOT_TOKEN": "t"})
    assert s.api_key == "abc" and s.api_secret is None and not s.has_api_keys
    assert "abc" not in repr(s) and "abc" not in str(s)
    assert "api_key" not in str(BotConfig().printable())
