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
    maker_fee_rate: float = Field(0.001, ge=0.0, lt=0.1, description="maker fee, charged on limit fills")
    slippage_bps: float = Field(5.0, ge=0.0, description="paper/backtest slippage in basis points")
    order_type: Literal["market", "limit"] = Field(
        "market", description="market crosses the spread; limit posts and may not fill")
    limit_offset_bps: float = Field(
        5.0, ge=0.0, description="how far below (buy) or above (sell) the price a limit order is posted")
    limit_fallback_market: bool = Field(
        False, description="cross the spread with a market order when a limit order does not fill")
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


class ExitsConfig(StrictModel):
    """How an open position is managed after the entry. All multiples are of the ATR
    measured when the position was opened. Zero turns a rule off."""

    trailing_atr_mult: float = Field(0.0, ge=0.0, description="trail the stop this far below the highest price")
    breakeven_after_atr: float = Field(0.0, ge=0.0, description="move the stop to entry once this much in profit")
    partial_take_fraction: float = Field(0.0, ge=0.0, le=0.9, description="fraction of the position to sell early")
    partial_take_atr: float = Field(1.0, gt=0.0, description="profit at which the partial sale happens")


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
    telegram_commands: bool = Field(True, description="obey /status, /stop, /kill... from the configured chat")
    watchdog_minutes: float = Field(15.0, ge=0.0, description="alert when no trading cycle for this long; 0 = off")
    daily_report_hour_utc: int = Field(-1, ge=-1, le=23, description="send a daily summary at this UTC hour; -1 = off")


class UpdateConfig(StrictModel):
    enabled: bool = True
    auto_install: bool = Field(True, description="install a new build automatically while the bot is stopped")
    check_interval_minutes: float = Field(15.0, ge=1.0)
    repo: str = "YASARAMA/BTC-bot-auto-trading"


class LoggingConfig(StrictModel):
    level: str = "INFO"
    format: Literal["json", "text"] = "json"


class BotConfig(StrictModel):
    mode: Literal["safe", "balanced", "aggressive"] = "balanced"
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    exits: ExitsConfig = Field(default_factory=ExitsConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)

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


DOTENV_KEYS = ("EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "EXCHANGE_API_PASSWORD", "LIVE_TRADING",
               "BOT_CONFIG", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL", "GITHUB_TOKEN",
               "ANTHROPIC_API_KEY")


def read_dotenv(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse KEY=VALUE lines (quotes optional, # comments). Missing file -> {}."""
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def load_dotenv(path: str | os.PathLike[str], override: bool = False) -> dict[str, str]:
    """Load a .env file into os.environ. Existing non-empty variables win unless override."""
    values = read_dotenv(path)
    for k, v in values.items():
        if override or not os.environ.get(k):
            os.environ[k] = v
    return values


def write_dotenv(path: str | os.PathLike[str], updates: Mapping[str, str | None]) -> None:
    """Merge updates into the .env file. None removes a key. File is created with owner-only perms."""
    p = Path(path)
    current = read_dotenv(p)
    for k, v in updates.items():
        if v is None:
            current.pop(k, None)
        else:
            current[k] = v
    lines = ["# Written by the BTC bot UI. Keep this file private: it can hold API keys."]
    for k in DOTENV_KEYS:
        if k in current:
            lines.append(f"{k}={current.pop(k)}")
    for k, v in current.items():
        lines.append(f"{k}={v}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


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
