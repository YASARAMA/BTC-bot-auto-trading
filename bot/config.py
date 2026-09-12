"""Configuration: config.yaml (pydantic) plus secrets from the environment.

Secrets never live in config.yaml and are never logged. Live trading needs two
independent switches: LIVE_TRADING=true in the environment AND exchange.live: true
in config.yaml. Any other combination refuses to start live.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_CONFIG_PATH = "config.yaml"
ENV_CONFIG_PATH = "BOT_CONFIG"

Mode = Literal["paper", "live"]


class LiveSafetyError(RuntimeError):
    """Raised when the live-trading switches are inconsistent or incomplete."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExchangeConfig(StrictModel):
    id: str = "binance"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    live: bool = False
    fee_rate: float = Field(0.001, ge=0.0, lt=0.1, description="taker fee as a fraction")
    slippage_bps: float = Field(5.0, ge=0.0, description="paper/backtest slippage in basis points")
    candle_history: int = Field(500, ge=50, description="closed candles kept in the rolling window")
    poll_interval_seconds: float = Field(10.0, gt=0.0)
    candle_close_grace_seconds: float = Field(5.0, ge=0.0, description="wait after a close before fetching")
    order_timeout_seconds: float = Field(30.0, gt=0.0)
    order_poll_seconds: float = Field(1.0, ge=0.0)
    max_retries: int = Field(5, ge=0)
    backoff_base_seconds: float = Field(1.0, gt=0.0)
    backoff_max_seconds: float = Field(60.0, gt=0.0)
    sandbox: bool = Field(False, description="use the exchange testnet when ccxt supports it")

    @field_validator("timeframe")
    @classmethod
    def _check_timeframe(cls, v: str) -> str:
        from bot.common import timeframe_to_ms

        timeframe_to_ms(v)
        return v

    @field_validator("symbol")
    @classmethod
    def _check_symbol(cls, v: str) -> str:
        if "/" not in v:
            raise ValueError("symbol must look like BASE/QUOTE, e.g. BTC/USDT")
        return v

    @property
    def base(self) -> str:
        return self.symbol.split("/")[0]

    @property
    def quote(self) -> str:
        return self.symbol.split("/")[1].split(":")[0]


class StrategyConfig(StrictModel):
    name: str = "ema_rsi"
    params: dict[str, Any] = Field(default_factory=dict)


class RiskConfig(StrictModel):
    risk_per_trade_pct: float = Field(1.0, gt=0.0, le=100.0)
    max_position_pct: float = Field(25.0, gt=0.0, le=100.0)
    max_daily_loss_pct: float = Field(3.0, gt=0.0, le=100.0)
    max_consecutive_losses: int = Field(3, ge=1)
    cooldown_minutes: float = Field(240.0, ge=0.0)
    min_seconds_between_trades: float = Field(3600.0, ge=0.0)
    min_confidence: float = Field(0.5, ge=0.0, le=1.0)
    min_order_notional: float = Field(10.0, ge=0.0)
    kill_switch_file: str = "./data/KILL_SWITCH"


class PaperConfig(StrictModel):
    initial_cash: float = Field(10_000.0, gt=0.0)
    initial_base: float = Field(0.0, ge=0.0)


class StateConfig(StrictModel):
    db_path: str = "./data/bot.sqlite"


class NotifyConfig(StrictModel):
    on_fill: bool = True
    on_halt: bool = True
    on_error: bool = True
    timeout_seconds: float = Field(10.0, gt=0.0)


class LoggingConfig(StrictModel):
    level: str = "INFO"
    format: Literal["json", "text"] = "json"


class BotConfig(StrictModel):
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def printable(self) -> dict[str, Any]:
        """Config as a dict. It holds no secrets by construction, so it is safe to log."""
        return self.model_dump(mode="json")


class Secrets(BaseModel):
    """Values read from the environment only. Never log or serialise this object."""

    model_config = ConfigDict(frozen=True)

    api_key: str | None = None
    api_secret: str | None = None
    api_password: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return "Secrets(<redacted>)"

    __str__ = __repr__

    @property
    def has_api_keys(self) -> bool:
        return bool(self.api_key and self.api_secret)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: str | os.PathLike[str] | None = None) -> BotConfig:
    cfg_path = Path(path or os.environ.get(ENV_CONFIG_PATH) or DEFAULT_CONFIG_PATH)
    with cfg_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{cfg_path}: top level must be a mapping")
    return BotConfig.model_validate(raw)


def load_secrets(env: Mapping[str, str] | None = None) -> Secrets:
    e = os.environ if env is None else env

    def get(name: str) -> str | None:
        v = e.get(name)
        return v.strip() if v and v.strip() else None

    return Secrets(
        api_key=get("EXCHANGE_API_KEY"),
        api_secret=get("EXCHANGE_API_SECRET"),
        api_password=get("EXCHANGE_API_PASSWORD"),
        telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=get("TELEGRAM_CHAT_ID"),
        discord_webhook_url=get("DISCORD_WEBHOOK_URL"),
    )


def resolve_mode(cfg: BotConfig, secrets: Secrets, env: Mapping[str, str] | None = None) -> Mode:
    """Decide paper vs live. Raises LiveSafetyError unless the switches agree."""
    e = os.environ if env is None else env
    env_live = _truthy(e.get("LIVE_TRADING"))
    cfg_live = cfg.exchange.live
    if not env_live and not cfg_live:
        return "paper"
    if env_live and not cfg_live:
        raise LiveSafetyError(
            "LIVE_TRADING=true is set but config.yaml has exchange.live: false. "
            "Refusing to start. Set both to go live, or unset LIVE_TRADING for paper mode."
        )
    if cfg_live and not env_live:
        raise LiveSafetyError(
            "config.yaml has exchange.live: true but LIVE_TRADING is not 'true' in the environment. "
            "Refusing to start. Set both to go live, or set exchange.live: false for paper mode."
        )
    if not secrets.has_api_keys:
        raise LiveSafetyError(
            "Live mode requires EXCHANGE_API_KEY and EXCHANGE_API_SECRET in the environment."
        )
    return "live"
