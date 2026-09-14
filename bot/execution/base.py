"""Exchange interfaces. Paper and live implementations share these so the engine
never knows which one it is talking to."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from bot.models import Order, Side


@dataclass(frozen=True)
class MarketInfo:
    amount_step: float | None = None   # quantity granularity (e.g. 0.00001 BTC)
    price_step: float | None = None
    min_amount: float = 0.0
    min_notional: float = 0.0


class MarketData(ABC):
    """Read-only market access: candles, last price, symbol metadata, clock."""

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None) -> list[list[float]]:
        """Return [[ts_ms, open, high, low, close, volume], ...] oldest first. May include the open candle."""

    @abstractmethod
    def fetch_ticker_price(self, symbol: str) -> float:
        ...

    @abstractmethod
    def market_info(self, symbol: str) -> MarketInfo:
        ...

    def now_ms(self) -> int:
        from bot.common import now_ms

        return now_ms()


class ExchangeClient(MarketData):
    """Market data plus account access. Spot only: market orders, balances, open orders."""

    @abstractmethod
    def fetch_balance(self) -> dict[str, dict[str, float]]:
        """{asset: {"free": x, "total": y}}"""

    @abstractmethod
    def create_market_order(
        self, symbol: str, side: Side, amount: float, client_order_id: str, ref_price: float | None = None
    ) -> Order:
        ...

    def create_limit_order(
        self, symbol: str, side: Side, amount: float, price: float, client_order_id: str
    ) -> Order:
        """Post a resting order at `price`. Optional: not every client supports it."""
        raise NotImplementedError(f"{type(self).__name__} cannot place limit orders")

    @property
    def supports_limit_orders(self) -> bool:
        return type(self).create_limit_order is not ExchangeClient.create_limit_order

    @abstractmethod
    def fetch_order(self, order: Order) -> Order:
        """Refresh an order's status/fill from the exchange."""

    @abstractmethod
    def cancel_order(self, order: Order) -> Order:
        ...

    @abstractmethod
    def fetch_open_orders(self, symbol: str) -> list[Order]:
        ...
