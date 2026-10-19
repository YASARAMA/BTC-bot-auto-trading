from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, apply_overrides, load_settings
from app.core import limits
from tests.conftest import REPO_CONFIG


def test_repository_config_loads_with_paper_defaults() -> None:
    s = load_settings(REPO_CONFIG)
    assert s.app.mode == "paper"
    assert s.exchange.pairs == ["BTC/USDT", "ETH/USDT"]
    assert "SOL/USDT" in s.exchange.all_pairs
    assert s.exchange.signal_timeframe == "15m"
    assert s.exchange.confirm_timeframes == ["1h", "4h"]
    assert s.portfolio.initial_cash == 10_000
    assert s.risk.risk_per_trade_pct == 0.25
    assert s.risk.max_risk_per_trade_pct == 0.50
    assert s.risk.max_open_risk_pct == 1.0
    assert s.risk.daily_loss_limit_pct == 1.0
    assert s.risk.max_drawdown_pct == 5.0
    assert s.risk.leverage_enabled is False and s.risk.shorting_enabled is False
    assert s.costs.fee_pct_per_side == 0.10 and s.costs.slippage_pct_per_side == 0.05


@pytest.mark.parametrize("mode", ["live", "LIVE", "real", "production", "margin"])
def test_live_mode_cannot_be_configured(mode: str) -> None:
    with pytest.raises(ValidationError, match="live trading is not implemented"):
        Settings.model_validate({"app": {"mode": mode}})


@pytest.mark.parametrize("field", ["leverage_enabled", "shorting_enabled"])
def test_leverage_and_shorting_cannot_be_enabled(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"risk": {field: True}})


def test_hard_limits_cap_configuration() -> None:
    too_much = limits.HARD_MAX_RISK_PER_TRADE_PCT + 0.01
    with pytest.raises(ValidationError):
        Settings.model_validate({"risk": {"risk_per_trade_pct": too_much, "max_risk_per_trade_pct": too_much,
                                          "max_open_risk_pct": 3.0}})
    with pytest.raises(ValidationError):
        Settings.model_validate({"risk": {"max_drawdown_pct": limits.HARD_MAX_DRAWDOWN_PCT + 1}})


def test_inconsistent_risk_rejected() -> None:
    with pytest.raises(ValidationError, match="above max_risk_per_trade_pct"):
        Settings.model_validate({"risk": {"risk_per_trade_pct": 0.5, "max_risk_per_trade_pct": 0.3}})
    with pytest.raises(ValidationError, match="open-risk budget"):
        Settings.model_validate({"risk": {"max_risk_per_trade_pct": 0.9, "max_open_risk_pct": 0.5,
                                          "risk_per_trade_pct": 0.25}})


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"risk": {"risk_per_trade": 0.25}})


def test_wildcard_cors_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"server": {"cors_origins": ["*"]}})


def test_confirmation_timeframes_must_be_multiples() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"exchange": {"signal_timeframe": "1h", "confirm_timeframes": ["15m"]}})


def test_no_trade_state_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"selector": {"allow_no_trade": False}})


def test_round_trip_cost() -> None:
    s = Settings()
    assert s.costs.round_trip_pct() == pytest.approx(0.32)
    assert s.costs.round_trip_pct(2.0) == pytest.approx(0.64)


def test_overrides_validated_like_the_file() -> None:
    base = Settings()
    changed = apply_overrides(base, {"risk": {"risk_per_trade_pct": 0.1}})
    assert changed.risk.risk_per_trade_pct == 0.1
    with pytest.raises(ValueError):
        apply_overrides(base, {"risk": {"leverage_enabled": True}})       # not editable at runtime
    with pytest.raises(ValueError):
        apply_overrides(base, {"server": {"port": 9000}})                 # whole section not editable
    with pytest.raises(ValueError):
        apply_overrides(base, {"risk": {"risk_per_trade_pct": 5.0}})      # beyond the hard limit
