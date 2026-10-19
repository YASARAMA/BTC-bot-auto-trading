"""Configuration: one YAML file, validated into typed models at startup.

Two rules this module enforces by construction, because they are the difference between a
research platform and an accident: leverage and shorting cannot be switched on, and the
application mode cannot be "live". There is no code path in this project that submits a
real-money order; these checks make sure a config file cannot pretend otherwise.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config.yaml"
ENV_CONFIG = "CAT_CONFIG"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppConfig(Strict):
    name: str = "Claude Adaptive Trader"
    mode: Literal["paper", "backtest"] = "paper"
    timezone_note: str = ""

    @field_validator("mode", mode="before")
    @classmethod
    def _no_live(cls, v: Any) -> Any:
        if str(v).lower() in {"live", "real", "production"}:
            raise ValueError(
                "live trading is not implemented in this version and cannot be enabled from config")
        return v


class ExchangeConfig(Strict):
    id: str = "binance"
    market_type: Literal["spot"] = "spot"
    quote: str = "USDT"
    pairs: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    extra_pairs: list[str] = Field(default_factory=list)
    signal_timeframe: str = "15m"
    confirm_timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h"])
    timeframes: list[str] = Field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "1d"])
    rate_limit_ms: int = Field(1200, ge=0)
    stale_after_seconds: float = Field(180.0, gt=0)
    history_days: int = Field(400, ge=1)

    @property
    def all_pairs(self) -> list[str]:
        seen: list[str] = []
        for pair in [*self.pairs, *self.extra_pairs]:
            if pair not in seen:
                seen.append(pair)
        return seen


class CostConfig(Strict):
    """Research assumptions. They are deliberately pessimistic and configurable."""

    fee_pct_per_side: float = Field(0.10, ge=0.0, lt=5.0)
    slippage_pct_per_side: float = Field(0.05, ge=0.0, lt=5.0)
    spread_pct: float = Field(0.02, ge=0.0, lt=5.0)
    stress_multipliers: list[float] = Field(default_factory=lambda: [1.0, 2.0, 3.0])

    def round_trip_pct(self, multiplier: float = 1.0) -> float:
        """What a complete trade costs before it has made anything, as a percentage."""
        per_side = (self.fee_pct_per_side + self.slippage_pct_per_side) * multiplier
        return per_side * 2 + self.spread_pct * multiplier


class PortfolioConfig(Strict):
    initial_cash: float = Field(10_000.0, gt=0)
    currency: str = "USDT"


class RiskConfig(Strict):
    risk_per_trade_pct: float = Field(0.25, gt=0.0, le=5.0)
    max_risk_per_trade_pct: float = Field(0.50, gt=0.0, le=5.0)
    max_open_risk_pct: float = Field(1.0, gt=0.0, le=20.0)
    max_position_pct: float = Field(20.0, gt=0.0, le=100.0)
    max_positions: int = Field(3, ge=1, le=20)
    max_correlated_positions: int = Field(2, ge=1, le=20)
    daily_loss_limit_pct: float = Field(1.0, gt=0.0, le=50.0)
    max_drawdown_pct: float = Field(5.0, gt=0.0, le=90.0)
    min_order_notional: float = Field(10.0, ge=0.0)
    leverage_enabled: bool = False
    shorting_enabled: bool = False
    kill_switch: bool = False

    @model_validator(mode="after")
    def _check(self) -> "RiskConfig":
        if self.leverage_enabled:
            raise ValueError("leverage is not implemented and must stay disabled")
        if self.shorting_enabled:
            raise ValueError("short selling is not implemented and must stay disabled")
        if self.risk_per_trade_pct > self.max_risk_per_trade_pct:
            raise ValueError("risk_per_trade_pct is above max_risk_per_trade_pct")
        if self.max_risk_per_trade_pct > self.max_open_risk_pct:
            raise ValueError("a single trade may not risk more than the whole open-risk budget")
        return self


class RegimeConfig(Strict):
    ema_fast: int = Field(20, ge=2)
    ema_mid: int = Field(50, ge=2)
    ema_slow: int = Field(200, ge=2)
    atr_period: int = Field(14, ge=2)
    adx_period: int = Field(14, ge=2)
    trend_adx_min: float = Field(22.0, gt=0)
    range_adx_max: float = Field(18.0, gt=0)
    squeeze_atr_percentile: float = Field(25.0, ge=0, le=100)
    expansion_atr_percentile: float = Field(75.0, ge=0, le=100)
    slope_lookback: int = Field(10, ge=1)
    min_confidence: float = Field(0.55, ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "RegimeConfig":
        if not self.ema_fast < self.ema_mid < self.ema_slow:
            raise ValueError("ema_fast < ema_mid < ema_slow is required")
        if self.range_adx_max > self.trend_adx_min:
            raise ValueError("range_adx_max must not exceed trend_adx_min")
        return self


class SelectorConfig(Strict):
    min_trades_for_eligibility: int = Field(30, ge=1)
    min_profit_factor: float = Field(1.20, ge=0)
    min_expectancy: float = Field(0.0)
    max_drawdown_pct: float = Field(10.0, gt=0)
    hysteresis_evaluations: int = Field(3, ge=1)
    allow_no_trade: bool = True


class SplitConfig(Strict):
    development: float = Field(0.5, gt=0, lt=1)
    validation: float = Field(0.25, gt=0, lt=1)
    test: float = Field(0.25, gt=0, lt=1)

    @model_validator(mode="after")
    def _sums(self) -> "SplitConfig":
        total = self.development + self.validation + self.test
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"the three splits must sum to 1.0, got {total}")
        return self


class BacktestConfig(Strict):
    split: SplitConfig = Field(default_factory=SplitConfig)
    walk_forward_windows: int = Field(5, ge=1)
    train_ratio: float = Field(0.7, gt=0, lt=1)
    min_trades_per_window: int = Field(5, ge=1)


class ResearchConfig(Strict):
    claude_enabled: bool = False
    model: str = "claude-opus-5"
    max_output_tokens: int = Field(4000, ge=256)


class StorageConfig(Strict):
    database: str = "data/trader.sqlite"
    candles_dir: str = "data/candles"
    reports_dir: str = "reports"


class ServerConfig(Strict):
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


class Settings(Strict):
    app: AppConfig = Field(default_factory=AppConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    costs: CostConfig = Field(default_factory=CostConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    selector: SelectorConfig = Field(default_factory=SelectorConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    # ----- paths -------------------------------------------------------------------
    @property
    def root(self) -> Path:
        return ROOT

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else ROOT / p

    @property
    def database_path(self) -> Path:
        return self.path(self.storage.database)

    @property
    def candles_path(self) -> Path:
        return self.path(self.storage.candles_dir)

    @property
    def reports_path(self) -> Path:
        return self.path(self.storage.reports_dir)

    def printable(self) -> dict[str, Any]:
        """Safe to log and to send to the browser: this config holds no secrets."""
        return self.model_dump(mode="json")


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    cfg_path = Path(path or os.environ.get(ENV_CONFIG) or DEFAULT_CONFIG)
    with cfg_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{cfg_path}: the top level must be a mapping")
    return Settings.model_validate(raw)


_cached: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings, loaded once. Tests can pass their own to each component."""
    global _cached
    if _cached is None:
        _cached = load_settings()
    return _cached


def reset_settings_cache() -> None:
    global _cached
    _cached = None
