"""Core value objects shared across modules."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


OrderStatus = Literal["open", "closed", "canceled", "rejected"]
IntentKind = Literal["entry", "exit", "stop_loss", "take_profit"]


@dataclass(frozen=True)
class Signal:
    """Output of a strategy for one closed candle."""

    action: Action
    confidence: float = 0.0
    reason: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence}")

    @classmethod
    def hold(cls, reason: str) -> "Signal":
        return cls(Action.HOLD, 0.0, reason)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d


@dataclass
class Position:
    qty: float
    entry_price: float
    entry_ts: int
    stop_loss: float | None
    take_profit: float | None
    entry_order_id: str
    strategy: str
    fees_paid: float = 0.0
    # exit management
    atr_at_entry: float = 0.0
    highest_price: float = 0.0
    initial_stop: float | None = None
    initial_qty: float = 0.0
    partial_done: bool = False

    def notional(self, price: float) -> float:
        return self.qty * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.entry_price) * self.qty

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Position":
        # Ignore unknown keys so a state file written by an older version still loads.
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True)
class OrderIntent:
    side: Side
    qty: float
    ref_price: float
    kind: IntentKind
    candle_ts: int
    reason: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d


@dataclass(frozen=True)
class Rejection:
    reason: str
    code: str = "rejected"


@dataclass
class Order:
    client_order_id: str
    symbol: str
    side: Side
    type: str
    amount: float
    status: OrderStatus
    created_at: int
    updated_at: int
    exchange_order_id: str | None = None
    price: float | None = None  # the limit price; None for market orders
    filled: float = 0.0
    avg_price: float | None = None
    fee: float = 0.0
    candle_ts: int | None = None
    kind: str = ""
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_final(self) -> bool:
        return self.status in ("closed", "canceled", "rejected")

    @property
    def remaining(self) -> float:
        return max(0.0, self.amount - self.filled)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        d.pop("raw", None)
        return d


@dataclass
class Trade:
    symbol: str
    strategy: str
    qty: float
    entry_ts: int
    entry_price: float
    exit_ts: int
    exit_price: float
    fees: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    entry_order_id: str
    exit_order_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
