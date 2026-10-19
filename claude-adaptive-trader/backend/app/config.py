"""Configuration: one YAML file, validated into typed models at startup.

Rules this module enforces by construction, because they are the difference between a
research platform and an accident: leverage and shorting cannot be switched on, the
application mode cannot be "live", and no threshold can be set beyond the hard limits in
``app.core.limits``. There is no code path in this project that submits a real-money
order; these checks make sure a config file cannot pretend otherwise.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core import limits
from app.core.hashing import stable_hash
from app.core.timeframes import TIMEFRAME_MS, can_derive, tf_ms

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config.yaml"
ENV_CONFIG = "CAT_CONFIG"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_timeframe(value: str) -> str:
    if value not in TIMEFRAME_MS:
        raise ValueError(f"unsupported timeframe {value!r}; use one of {', '.join(TIMEFRAME_MS)}")
    return value


def _check_pair(value: str) -> str:
    parts = value.split("/")
    if len(parts) != 2 or not all(p.isalnum() and p.isupper() for p in parts):
        raise ValueError(f"pair {value!r} must look like BTC/USDT")
    return value


class AppConfig(Strict):
    name: str = "Claude Adaptive Trader"
    mode: Literal["paper", "backtest"] = "paper"

    @field_validator("mode", mode="before")
    @classmethod
    def _no_live(cls, v: Any) -> Any:
        if str(v).lower() not in limits.SUPPORTED_MODES:
            raise ValueError(
                f"mode {v!r} is not available: live trading is not implemented in this version")
        return str(v).lower()


class ExchangeConfig(Strict):
    id: Literal["binance"] = "binance"
    market_type: Literal["spot"] = "spot"
    quote: str = "USDT"
    pairs: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"], min_length=1)
    extra_pairs: list[str] = Field(default_factory=list)
    signal_timeframe: str = "15m"
    confirm_timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h"])
    timeframes: list[str] = Field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "1d"])
    rate_limit_ms: int = Field(1200, ge=50)
    request_timeout_ms: int = Field(15000, ge=1000, le=120000)
    stale_after_seconds: float = Field(180.0, gt=0)
    history_days: int = Field(400, ge=1, le=3650)
    websocket_enabled: bool = True
    websocket_url: str = "wss://stream.binance.com:9443/stream"
    ticker_poll_seconds: float = Field(10.0, ge=1.0)
    candle_poll_seconds: float = Field(30.0, ge=5.0)

    @field_validator("pairs", "extra_pairs")
    @classmethod
    def _pairs(cls, v: list[str]) -> list[str]:
        return [_check_pair(p) for p in v]

    @field_validator("signal_timeframe")
    @classmethod
    def _signal_tf(cls, v: str) -> str:
        return _check_timeframe(v)

    @field_validator("confirm_timeframes", "timeframes")
    @classmethod
    def _tfs(cls, v: list[str]) -> list[str]:
        return [_check_timeframe(t) for t in v]

    @field_validator("websocket_url")
    @classmethod
    def _wss(cls, v: str) -> str:
        if not v.startswith("wss://"):
            raise ValueError("the market-data stream must use wss://")
        return v

    @model_validator(mode="after")
    def _confirmations(self) -> "ExchangeConfig":
        for tf in self.confirm_timeframes:
            if not can_derive(self.signal_timeframe, tf):
                raise ValueError(
                    f"confirmation timeframe {tf} must be a whole multiple of the signal timeframe "
                    f"{self.signal_timeframe}")
        return self

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
    stress_multipliers: list[float] = Field(default_factory=lambda: [1.0, 2.0, 3.0], min_length=1)
    take_profit_trade_through_pct: float = Field(0.02, ge=0.0, lt=5.0)
    max_participation_pct: float = Field(5.0, gt=0.0, le=100.0)
    market_impact: bool = True

    @field_validator("stress_multipliers")
    @classmethod
    def _mults(cls, v: list[float]) -> list[float]:
        if any(m <= 0 for m in v):
            raise ValueError("cost multipliers must be positive")
        return sorted(set(v))

    def round_trip_pct(self, multiplier: float = 1.0) -> float:
        """What a complete trade costs before it has made anything, as a percentage."""
        per_side = (self.fee_pct_per_side + self.slippage_pct_per_side) * multiplier
        return per_side * 2 + self.spread_pct * multiplier


class PortfolioConfig(Strict):
    initial_cash: float = Field(10_000.0, gt=0, le=1e9)
    currency: str = "USDT"


class RiskConfig(Strict):
    risk_per_trade_pct: float = Field(0.25, gt=0.0, le=limits.HARD_MAX_RISK_PER_TRADE_PCT)
    max_risk_per_trade_pct: float = Field(0.50, gt=0.0, le=limits.HARD_MAX_RISK_PER_TRADE_PCT)
    max_open_risk_pct: float = Field(1.0, gt=0.0, le=limits.HARD_MAX_OPEN_RISK_PCT)
    max_position_pct: float = Field(20.0, gt=0.0, le=limits.HARD_MAX_POSITION_PCT)
    max_total_exposure_pct: float = Field(60.0, gt=0.0, le=limits.HARD_MAX_TOTAL_EXPOSURE_PCT)
    max_positions: int = Field(3, ge=1, le=limits.HARD_MAX_POSITIONS)
    max_correlated_positions: int = Field(2, ge=1, le=limits.HARD_MAX_POSITIONS)
    correlation_groups: list[list[str]] = Field(
        default_factory=lambda: [["BTC/USDT", "ETH/USDT", "SOL/USDT"]])
    daily_loss_limit_pct: float = Field(1.0, gt=0.0, le=limits.HARD_MAX_DAILY_LOSS_PCT)
    max_drawdown_pct: float = Field(5.0, gt=0.0, le=limits.HARD_MAX_DRAWDOWN_PCT)
    min_order_notional: float = Field(10.0, ge=0.0)
    min_stop_distance_pct: float = Field(0.15, ge=0.0, le=10.0)
    max_stop_distance_pct: float = Field(8.0, gt=0.0, le=50.0)
    max_cost_to_reward: float = Field(0.35, gt=0.0, le=1.0)
    max_spread_pct: float = Field(0.10, gt=0.0, le=5.0)
    liquidity_max_volume_pct: float = Field(2.0, gt=0.0, le=100.0)
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
        if self.max_position_pct > self.max_total_exposure_pct:
            raise ValueError("max_position_pct cannot exceed max_total_exposure_pct")
        if self.min_stop_distance_pct >= self.max_stop_distance_pct:
            raise ValueError("min_stop_distance_pct must be below max_stop_distance_pct")
        for group in self.correlation_groups:
            for pair in group:
                _check_pair(pair)
        return self

    def correlated_with(self, pair: str) -> set[str]:
        """Every pair that shares a correlation group with ``pair`` (including itself)."""
        out = {pair}
        for group in self.correlation_groups:
            if pair in group:
                out.update(group)
        return out


class RegimeConfig(Strict):
    ema_fast: int = Field(20, ge=2)
    ema_mid: int = Field(50, ge=2)
    ema_slow: int = Field(200, ge=2)
    atr_period: int = Field(14, ge=2)
    adx_period: int = Field(14, ge=2)
    rsi_period: int = Field(14, ge=2)
    bollinger_period: int = Field(20, ge=2)
    bollinger_std: float = Field(2.0, gt=0)
    volume_period: int = Field(20, ge=2)
    range_period: int = Field(20, ge=2)
    rank_window: int = Field(500, ge=50)
    trend_adx_min: float = Field(22.0, gt=0)
    range_adx_max: float = Field(18.0, gt=0)
    trend_slope_min_atr: float = Field(1.0, gt=0)
    range_slope_max_atr: float = Field(0.6, gt=0)
    squeeze_percentile: float = Field(25.0, ge=0, le=100)
    expansion_percentile: float = Field(75.0, ge=0, le=100)
    expansion_range_atr: float = Field(1.5, gt=0)
    shock_range_atr: float = Field(4.0, gt=0)
    shock_percentile: float = Field(99.0, ge=0, le=100)
    squeeze_lookback: int = Field(20, ge=1)
    slope_lookback: int = Field(10, ge=1)
    min_confidence: float = Field(0.55, ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "RegimeConfig":
        if not self.ema_fast < self.ema_mid < self.ema_slow:
            raise ValueError("ema_fast < ema_mid < ema_slow is required")
        if self.range_adx_max > self.trend_adx_min:
            raise ValueError("range_adx_max must not exceed trend_adx_min")
        if self.range_slope_max_atr > self.trend_slope_min_atr:
            raise ValueError("range_slope_max_atr must not exceed trend_slope_min_atr")
        if self.squeeze_percentile >= self.expansion_percentile:
            raise ValueError("squeeze_percentile must be below expansion_percentile")
        if self.expansion_range_atr >= self.shock_range_atr:
            raise ValueError("expansion_range_atr must be below shock_range_atr")
        return self


class SelectorConfig(Strict):
    min_trades_for_eligibility: int = Field(30, ge=1)
    min_profit_factor: float = Field(1.20, ge=0)
    min_expectancy_r: float = Field(0.0)
    max_drawdown_pct: float = Field(10.0, gt=0)
    stress_multiplier: float = Field(2.0, ge=1.0)
    hysteresis_evaluations: int = Field(3, ge=1)
    switch_margin: float = Field(0.05, ge=0)
    recent_trades_window: int = Field(20, ge=1)
    recent_min_trades: int = Field(10, ge=1)
    complexity_penalty: float = Field(0.01, ge=0)
    allow_no_trade: bool = True

    @field_validator("allow_no_trade")
    @classmethod
    def _no_trade(cls, v: bool) -> bool:
        if not v:
            raise ValueError("the no-trade state cannot be disabled: abstaining must always be possible")
        return v


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
    walk_forward_windows: int = Field(5, ge=1, le=50)
    train_ratio: float = Field(0.7, gt=0, lt=1)
    min_trades_per_window: int = Field(5, ge=1)
    breakers: Literal["record", "enforce"] = "record"
    bootstrap_samples: int = Field(2000, ge=100, le=100_000)
    seed: int = 42
    random_baseline_runs: int = Field(20, ge=1, le=500)


class GateConfig(Strict):
    min_trades: int = Field(300, ge=1)
    min_profit_factor: float = Field(1.20, ge=0)
    min_expectancy_r: float = Field(0.0)
    stress_multiplier: float = Field(2.0, ge=1.0)
    min_parameter_stability: float = Field(0.6, ge=0, le=1)
    min_profitable_environments: int = Field(2, ge=1)
    min_trades_per_environment: int = Field(10, ge=1)
    min_profitable_window_ratio: float = Field(0.6, ge=0, le=1)
    max_best_month_share: float = Field(0.5, gt=0, le=1)
    require_ci_above_zero: bool = True


class PaperConfig(Strict):
    poll_seconds: float = Field(5.0, ge=0.5, le=300)
    auto_resume: bool = True
    replay_bars_per_second: float = Field(20.0, gt=0, le=10_000)


class ResearchConfig(Strict):
    claude_enabled: bool = False
    model: str = ""
    max_output_tokens: int = Field(4000, ge=256, le=64_000)


class StorageConfig(Strict):
    database: str = "data/trader.sqlite"
    market_database: str = "data/market.sqlite"
    imports_dir: str = "data/imports"
    reports_dir: str = "reports"


class ServerConfig(Strict):
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @field_validator("cors_origins")
    @classmethod
    def _origins(cls, v: list[str]) -> list[str]:
        for origin in v:
            if origin == "*" or not origin.startswith(("http://", "https://")):
                raise ValueError(f"CORS origin {origin!r} must be an explicit http(s) origin, never '*'")
        return v


class Settings(Strict):
    app: AppConfig = Field(default_factory=AppConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    costs: CostConfig = Field(default_factory=CostConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    selector: SelectorConfig = Field(default_factory=SelectorConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    gates: GateConfig = Field(default_factory=GateConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
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
    def market_database_path(self) -> Path:
        return self.path(self.storage.market_database)

    @property
    def imports_path(self) -> Path:
        return self.path(self.storage.imports_dir)

    @property
    def reports_path(self) -> Path:
        return self.path(self.storage.reports_dir)

    def printable(self) -> dict[str, Any]:
        """Safe to log and to send to the browser: this config holds no secrets."""
        return self.model_dump(mode="json")

    def config_hash(self) -> str:
        return stable_hash(self.printable())

    def signal_tf_ms(self) -> int:
        return tf_ms(self.exchange.signal_timeframe)


# Settings the user may change from the browser. Everything else needs the config file.
EDITABLE_SECTIONS: dict[str, set[str]] = {
    "exchange": {"pairs", "extra_pairs", "stale_after_seconds", "history_days", "websocket_enabled",
                 "ticker_poll_seconds", "candle_poll_seconds"},
    "costs": {"fee_pct_per_side", "slippage_pct_per_side", "spread_pct", "take_profit_trade_through_pct",
              "max_participation_pct", "market_impact"},
    "risk": {"risk_per_trade_pct", "max_risk_per_trade_pct", "max_open_risk_pct", "max_position_pct",
             "max_total_exposure_pct", "max_positions", "max_correlated_positions", "daily_loss_limit_pct",
             "max_drawdown_pct", "min_order_notional", "min_stop_distance_pct", "max_stop_distance_pct",
             "max_cost_to_reward", "max_spread_pct", "liquidity_max_volume_pct"},
    "regime": {"trend_adx_min", "range_adx_max", "trend_slope_min_atr", "range_slope_max_atr",
               "squeeze_percentile", "expansion_percentile", "min_confidence"},
    "selector": {"min_trades_for_eligibility", "min_profit_factor", "min_expectancy_r", "max_drawdown_pct",
                 "stress_multiplier", "hysteresis_evaluations", "switch_margin"},
    "backtest": {"walk_forward_windows", "train_ratio", "breakers", "bootstrap_samples", "seed",
                 "random_baseline_runs"},
    "gates": {"min_trades", "min_profit_factor", "min_expectancy_r", "stress_multiplier",
              "min_parameter_stability", "min_profitable_environments", "min_profitable_window_ratio",
              "max_best_month_share", "require_ci_above_zero"},
    "paper": {"poll_seconds", "auto_resume", "replay_bars_per_second"},
    "research": {"claude_enabled", "model", "max_output_tokens"},
}


def apply_overrides(base: Settings, overrides: dict[str, dict[str, Any]]) -> Settings:
    """Return ``base`` with ``overrides`` applied, validated by exactly the same rules.

    Raises ``ValueError`` for a section or key that may not be edited at runtime; pydantic's
    ``ValidationError`` (a ``ValueError``) for a value that breaks a rule or a hard limit.
    """
    data = copy.deepcopy(base.model_dump(mode="python"))
    for section, values in overrides.items():
        allowed = EDITABLE_SECTIONS.get(section)
        if allowed is None:
            raise ValueError(f"section {section!r} cannot be changed at runtime")
        if not isinstance(values, dict):
            raise ValueError(f"section {section!r} must be a mapping")
        for key, value in values.items():
            if key not in allowed:
                raise ValueError(f"{section}.{key} cannot be changed at runtime")
            data[section][key] = value
    return Settings.model_validate(data)


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    cfg_path = Path(path or os.environ.get(ENV_CONFIG) or DEFAULT_CONFIG)
    with cfg_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{cfg_path}: the top level must be a mapping")
    return Settings.model_validate(raw)
