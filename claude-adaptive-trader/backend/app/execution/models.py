"""Domain records for simulated trading. Every amount is virtual money in the quote currency."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import CostConfig


@dataclass(frozen=True)
class Costs:
    """Cost model as fractions (0.001 = 0.1%)."""

    fee: float
    slippage: float
    half_spread: float
    take_profit_trade_through: float
    max_participation: float
    market_impact: bool
    multiplier: float = 1.0

    @classmethod
    def from_config(cls, cfg: CostConfig, multiplier: float = 1.0) -> "Costs":
        return cls(
            fee=cfg.fee_pct_per_side / 100 * multiplier,
            slippage=cfg.slippage_pct_per_side / 100 * multiplier,
            half_spread=cfg.spread_pct / 200 * multiplier,
            take_profit_trade_through=cfg.take_profit_trade_through_pct / 100,
            max_participation=cfg.max_participation_pct / 100,
            market_impact=cfg.market_impact,
            multiplier=multiplier,
        )

    @property
    def round_trip_pct(self) -> float:
        return ((self.fee + self.slippage + self.half_spread) * 2) * 100


@dataclass(frozen=True)
class Bar:
    pair: str
    ts: int                 # open time
    open: float
    high: float
    low: float
    close: float
    volume: float
    atr: float | None = None


@dataclass
class Order:
    client_order_id: str
    pair: str
    side: str               # buy | sell
    purpose: str            # entry | exit
    reason: str
    created_ts: int         # close time of the decision candle
    signal_ts: int          # open time of the decision candle
    qty: float
    ref_price: float        # the close the decision was taken at
    strategy: str
    strategy_version: str
    regime: str
    stop: float | None = None
    target: float | None = None
    invalidation: float | None = None
    time_stop_bars: int | None = None
    environment: str | None = None
    exit_reason: str | None = None
    status: str = "pending"
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FillResult:
    filled_qty: float
    price: float
    ref_price: float
    fee: float
    slippage_cost: float
    impact_pct: float
    partial: bool
    gapped: bool = False
    rejected: str | None = None


@dataclass
class Position:
    pair: str
    qty: float
    entry_price: float
    entry_ts: int
    entry_fee: float
    entry_slippage: float
    stop: float
    target: float | None
    invalidation: float | None
    initial_risk: float
    strategy: str
    strategy_version: str
    regime: str
    environment: str | None
    entry_reason: str
    signal_ts: int
    time_stop_bars: int | None
    key: str
    bars_held: int = 0
    high_water: float = 0.0
    low_water: float = 0.0
    exit_qty: float = 0.0
    exit_value: float = 0.0
    exit_fees: float = 0.0
    exit_slippage: float = 0.0
    pending_exit: bool = False
    id: int | None = None

    @property
    def remaining(self) -> float:
        return self.qty - self.exit_qty

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClosedTrade:
    position_key: str
    pair: str
    strategy: str
    strategy_version: str
    regime: str
    environment: str | None
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    qty: float
    stop_price: float
    take_profit: float | None
    gross_pnl: float
    fees: float
    slippage_cost: float
    net_pnl: float
    return_pct: float
    initial_risk: float
    r_multiple: float | None
    exit_reason: str
    entry_reason: str
    mae_pct: float
    mfe_pct: float
    bars_held: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("extra", None)
        return d
