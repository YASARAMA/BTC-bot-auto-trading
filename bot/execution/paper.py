"""PaperExchange: simulated balances and fills with slippage and fees.

Market data (candles, ticker) can come from a real exchange's public endpoints, a
CSV replay, or be set directly by the backtester via set_price(). Fills are always
simulated here, so no real order ever leaves this class.
"""
from __future__ import annotations

from typing import Any

from bot.common import round_step
from bot.execution.base import ExchangeClient, MarketData, MarketInfo
from bot.models import Order, Side


class PaperExchange(ExchangeClient):
    def __init__(
        self,
        *,
        symbol: str,
        fee_rate: float,
        slippage_bps: float,
        initial_cash: float,
        initial_base: float = 0.0,
        market_data: MarketData | None = None,
        market_info: MarketInfo | None = None,
        partial_fill_ratio: float = 1.0,
        clock: Any | None = None,
    ) -> None:
        self.symbol = symbol
        self.base, self.quote = symbol.split("/")[0], symbol.split("/")[1].split(":")[0]
        self.fee_rate = fee_rate
        self.slippage = slippage_bps / 10_000.0
        self.balances: dict[str, float] = {self.quote: float(initial_cash), self.base: float(initial_base)}
        self.market_data = market_data
        self._market_info = market_info or MarketInfo(amount_step=1e-6, min_amount=1e-5, min_notional=5.0)
        self.partial_fill_ratio = partial_fill_ratio
        self.orders: dict[str, Order] = {}
        self._price: float | None = None
        self._clock = clock
        self.fees_paid = 0.0

    # ----- clock / price -------------------------------------------------------------
    def now_ms(self) -> int:
        if self._clock is not None:
            return int(self._clock())
        if self.market_data is not None:
            return self.market_data.now_ms()
        return super().now_ms()

    def set_price(self, price: float) -> None:
        self._price = float(price)

    def set_balances(self, cash: float, base: float) -> None:
        self.balances[self.quote] = float(cash)
        self.balances[self.base] = float(base)

    # ----- MarketData ------------------------------------------------------------------
    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None) -> list[list[float]]:
        if self.market_data is None:
            raise RuntimeError("PaperExchange has no market data source")
        return self.market_data.fetch_ohlcv(symbol, timeframe, since, limit)

    def fetch_ticker_price(self, symbol: str) -> float:
        if self.market_data is not None:
            price = self.market_data.fetch_ticker_price(symbol)
            self._price = price
            return price
        if self._price is None:
            raise RuntimeError("PaperExchange price not set")
        return self._price

    def market_info(self, symbol: str) -> MarketInfo:
        if self.market_data is not None:
            try:
                return self.market_data.market_info(symbol)
            except Exception:  # noqa: BLE001 - fall back to defaults if metadata is unavailable
                pass
        return self._market_info

    # ----- account --------------------------------------------------------------------
    def fetch_balance(self) -> dict[str, dict[str, float]]:
        return {a: {"free": v, "total": v} for a, v in self.balances.items()}

    def create_market_order(
        self, symbol: str, side: Side, amount: float, client_order_id: str, ref_price: float | None = None
    ) -> Order:
        if client_order_id in self.orders:
            # Exchanges reject duplicate client ids; returning the original is equivalent
            # and keeps the executor's bookkeeping simple.
            return self.orders[client_order_id]
        price = float(ref_price) if ref_price is not None else self.fetch_ticker_price(symbol)
        now = self.now_ms()
        info = self.market_info(symbol)
        qty = round_step(float(amount), info.amount_step)
        order = Order(
            client_order_id=client_order_id, exchange_order_id=f"paper-{len(self.orders) + 1}",
            symbol=symbol, side=side, type="market", amount=qty, status="open", created_at=now, updated_at=now,
        )
        if qty <= 0 or qty < info.min_amount or qty * price < info.min_notional:
            order.status = "rejected"
            order.reason = f"amount {qty} below exchange minimums (min_amount={info.min_amount}, min_notional={info.min_notional})"
            self.orders[client_order_id] = order
            return order

        fill_qty = round_step(qty * self.partial_fill_ratio, info.amount_step)
        fill_price = price * (1.0 + self.slippage) if side == Side.BUY else price * (1.0 - self.slippage)
        notional = fill_qty * fill_price
        fee = notional * self.fee_rate

        if side == Side.BUY:
            if self.balances[self.quote] < notional + fee:
                order.status = "rejected"
                order.reason = f"insufficient {self.quote}: need {notional + fee:.2f}, have {self.balances[self.quote]:.2f}"
                self.orders[client_order_id] = order
                return order
            self.balances[self.quote] -= notional + fee
            self.balances[self.base] += fill_qty
        else:
            if self.balances[self.base] + 1e-12 < fill_qty:
                order.status = "rejected"
                order.reason = f"insufficient {self.base}: need {fill_qty}, have {self.balances[self.base]}"
                self.orders[client_order_id] = order
                return order
            self.balances[self.base] -= fill_qty
            self.balances[self.quote] += notional - fee

        self.fees_paid += fee
        order.filled = fill_qty
        order.avg_price = fill_price
        order.fee = fee
        order.status = "closed"  # market orders are immediate-or-cancel; unfilled remainder is gone
        order.updated_at = now
        self.orders[client_order_id] = order
        return order

    def fetch_order(self, order: Order) -> Order:
        return self.orders.get(order.client_order_id, order)

    def cancel_order(self, order: Order) -> Order:
        stored = self.orders.get(order.client_order_id, order)
        if stored.status == "open":
            stored.status = "canceled"
            stored.updated_at = self.now_ms()
        return stored

    def fetch_open_orders(self, symbol: str) -> list[Order]:
        return [o for o in self.orders.values() if o.status == "open" and o.symbol == symbol]
