"""PaperExchange: simulated balances and fills with slippage and fees.

Market data (candles, ticker) can come from a real exchange's public endpoints, a
CSV replay, or be set directly by the backtester via set_price(). Fills are always
simulated here, so no real order ever leaves this class.

Limit (maker) orders rest until the price reaches them. A backtest hands over the next
candle's range with set_fill_window(); a paper run that follows live prices checks each
new price as it arrives. An order the price never reaches simply does not fill - the
missed trade is the true cost of trying to save the taker fee, and pretending otherwise
would make every backtest of maker orders a lie.
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
        maker_fee_rate: float | None = None,
        market_data: MarketData | None = None,
        market_info: MarketInfo | None = None,
        partial_fill_ratio: float = 1.0,
        clock: Any | None = None,
    ) -> None:
        self.symbol = symbol
        self.base, self.quote = symbol.split("/")[0], symbol.split("/")[1].split(":")[0]
        self.fee_rate = fee_rate
        self.maker_fee_rate = fee_rate if maker_fee_rate is None else float(maker_fee_rate)
        self.slippage = slippage_bps / 10_000.0
        self.balances: dict[str, float] = {self.quote: float(initial_cash), self.base: float(initial_base)}
        self.market_data = market_data
        self._market_info = market_info or MarketInfo(amount_step=1e-6, min_amount=1e-5, min_notional=5.0)
        self.partial_fill_ratio = partial_fill_ratio
        self.orders: dict[str, Order] = {}
        self._price: float | None = None
        # Only orders still waiting are checked on a price change: a long backtest ends up
        # with thousands of finished ones, and walking all of them per candle is quadratic.
        self._resting: dict[str, Order] = {}
        self._fill_window: tuple[float, float] | None = None
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
        self._match_resting(price, price)

    def set_fill_window(self, high: float | None, low: float | None) -> None:
        """The price range a resting order gets to be touched by (one backtest candle).

        Cleared with (None, None), after which a resting order can only be filled by the
        prices that actually arrive through set_price.
        """
        self._fill_window = None if high is None or low is None else (float(high), float(low))

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

    def create_limit_order(
        self, symbol: str, side: Side, amount: float, price: float, client_order_id: str
    ) -> Order:
        if client_order_id in self.orders:
            return self.orders[client_order_id]
        now = self.now_ms()
        info = self.market_info(symbol)
        qty = round_step(float(amount), info.amount_step)
        limit = float(price)
        order = Order(
            client_order_id=client_order_id, exchange_order_id=f"paper-{len(self.orders) + 1}",
            symbol=symbol, side=side, type="limit", price=limit, amount=qty, status="open",
            created_at=now, updated_at=now,
        )
        if qty <= 0 or qty < info.min_amount or qty * limit < info.min_notional:
            order.status = "rejected"
            order.reason = (f"amount {qty} below exchange minimums (min_amount={info.min_amount}, "
                            f"min_notional={info.min_notional})")
        elif limit <= 0:
            order.status = "rejected"
            order.reason = f"limit price {limit} is not positive"
        self.orders[client_order_id] = order
        if order.status == "open":
            high, low = self._fill_window if self._fill_window else (self._price, self._price)
            if high is not None and low is not None:
                self._try_fill(order, float(high), float(low))
        if order.status == "open":
            self._resting[client_order_id] = order
        return self.orders[client_order_id]

    def _match_resting(self, high: float, low: float) -> None:
        for cid, order in list(self._resting.items()):
            if order.status != "open":
                self._resting.pop(cid, None)
                continue
            self._try_fill(order, high, low)
            if order.status != "open":
                self._resting.pop(cid, None)

    def _try_fill(self, order: Order, high: float, low: float) -> None:
        """A buy fills if the price traded down to it, a sell if it traded up to it.

        The fill happens at the limit price itself: a resting order is never filled at a
        worse price than it asked for, and it pays no slippage - that is the whole point
        of posting one. It pays the maker fee instead of the taker fee.
        """
        limit = order.price or 0.0
        if limit <= 0:
            return
        touched = low <= limit if order.side == Side.BUY else high >= limit
        if not touched:
            return
        info = self.market_info(order.symbol)
        fill_qty = round_step(order.amount * self.partial_fill_ratio, info.amount_step)
        if fill_qty <= 0:
            return
        notional = fill_qty * limit
        fee = notional * self.maker_fee_rate
        if order.side == Side.BUY:
            if self.balances[self.quote] < notional + fee:
                order.status = "rejected"
                order.reason = (f"insufficient {self.quote}: need {notional + fee:.2f}, "
                                f"have {self.balances[self.quote]:.2f}")
                order.updated_at = self.now_ms()
                return
            self.balances[self.quote] -= notional + fee
            self.balances[self.base] += fill_qty
        else:
            if self.balances[self.base] + 1e-12 < fill_qty:
                order.status = "rejected"
                order.reason = f"insufficient {self.base}: need {fill_qty}, have {self.balances[self.base]}"
                order.updated_at = self.now_ms()
                return
            self.balances[self.base] -= fill_qty
            self.balances[self.quote] += notional - fee
        self.fees_paid += fee
        order.filled = fill_qty
        order.avg_price = limit
        order.fee = fee
        order.status = "closed" if fill_qty >= order.amount - 1e-12 else "open"
        order.updated_at = self.now_ms()

    def fetch_order(self, order: Order) -> Order:
        stored = self.orders.get(order.client_order_id, order)
        if stored.status == "open" and stored.type == "limit":
            price = self._price
            if self.market_data is not None:
                # A paper run that follows live prices: ask for the newest one, so a resting
                # order gets the same chance to be reached as it would on the exchange.
                try:
                    price = self.fetch_ticker_price(self.symbol)
                except Exception:  # noqa: BLE001 - a stale price is better than a crash here
                    pass
            if price is not None:
                self._try_fill(stored, price, price)
            if stored.status != "open":
                self._resting.pop(stored.client_order_id, None)
        return stored

    def cancel_order(self, order: Order) -> Order:
        stored = self.orders.get(order.client_order_id, order)
        if stored.status == "open":
            stored.status = "canceled" if stored.filled <= 0 else "closed"
            stored.updated_at = self.now_ms()
        self._resting.pop(stored.client_order_id, None)
        return stored

    def fetch_open_orders(self, symbol: str) -> list[Order]:
        return [o for o in self.orders.values() if o.status == "open" and o.symbol == symbol]
