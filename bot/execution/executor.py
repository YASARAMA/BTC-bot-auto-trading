"""OrderExecutor: the only code path that places orders.

Responsibilities: idempotent ids, exchange precision/minimums, persisting every order
before and after it hits the exchange, waiting for fills, cancelling leftovers, and
returning what actually filled (which may be less than requested).

Two order types. A market order crosses the spread and pays the taker fee, and always
fills. A limit ("maker") order is posted a little away from the price and pays the lower
maker fee, but it only fills if the market comes to it: what it saves in fees it can lose
in missed trades. Which of those is the better deal depends on the strategy, so both are
available and the backtest models the misses rather than assuming them away."""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable

from bot.common import round_step
from bot.execution.base import ExchangeClient
from bot.execution.order_id import make_client_order_id
from bot.logging_utils import log_event
from bot.models import Order, OrderIntent, Side
from bot.state.store import StateStore

log = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(
        self,
        exchange: ExchangeClient,
        store: StateStore,
        *,
        symbol: str,
        strategy_name: str,
        order_timeout_seconds: float,
        order_poll_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], int] | None = None,
        quiet: bool = False,
        order_type: str = "market",
        limit_offset_bps: float = 0.0,
        limit_fallback_market: bool = False,
        wait_for_fill: bool = True,
    ) -> None:
        self.exchange = exchange
        self.store = store
        self.symbol = symbol
        self.strategy_name = strategy_name
        self.timeout = order_timeout_seconds
        self.poll = order_poll_seconds
        self._sleep = sleep
        self._clock = clock or exchange.now_ms
        self.quiet = quiet  # a backtest returns its orders in the result instead of logging them
        self.order_type = order_type
        self.limit_offset = limit_offset_bps / 10_000.0
        self.limit_fallback_market = limit_fallback_market
        # A backtest has no future to wait in: the candle the order was placed in already
        # decided whether it filled, so polling would only burn wall-clock time.
        self.wait_for_fill = wait_for_fill

    def _log(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        if self.quiet:
            return
        log_event(log, event, level=level, **fields)

    def client_order_id(self, intent: OrderIntent, attempt: int = 0) -> str:
        return make_client_order_id(self.strategy_name, self.symbol, intent.candle_ts, intent.side.value,
                                    attempt=attempt)

    def limit_price(self, intent: OrderIntent, price_step: float | None) -> float:
        """Where to rest the order: below the price to buy, above it to sell.

        Rounded away from the market (down for a buy, up for a sell) so rounding never
        turns a maker order into one that crosses the spread and pays the taker fee.
        """
        raw = (intent.ref_price * (1.0 - self.limit_offset) if intent.side == Side.BUY
               else intent.ref_price * (1.0 + self.limit_offset))
        if not price_step or price_step <= 0:
            return raw
        units = raw / price_step
        return round((math.floor(units) if intent.side == Side.BUY else math.ceil(units)) * price_step, 12)

    def wants_limit(self, intent: OrderIntent) -> bool:
        """Maker orders are for entries only.

        An exit - a stop, a take profit, a signal to get out - has to happen. Posting it
        and hoping the market comes back is how a small loss becomes a large one, so exits
        always cross the spread.
        """
        if self.order_type != "limit" or intent.kind != "entry":
            return False
        if not self.exchange.supports_limit_orders:
            self._log("limit_orders_unsupported", level=logging.WARNING,
                      exchange=type(self.exchange).__name__)
            return False
        return True

    def execute(self, intent: OrderIntent) -> Order | None:
        order = self._place(intent, attempt=0, use_limit=self.wants_limit(intent))
        if order is not None or not self.wants_limit(intent) or not self.limit_fallback_market:
            return order
        # The maker order never filled. Crossing the spread now costs the taker fee but
        # takes the trade the strategy asked for; skipping it is the alternative.
        self._log("limit_unfilled_fallback_market", level=logging.WARNING,
                  candle_ts=intent.candle_ts, side=intent.side.value, kind=intent.kind)
        return self._place(intent, attempt=1, use_limit=False)

    def _place(self, intent: OrderIntent, *, attempt: int, use_limit: bool) -> Order | None:
        cid = self.client_order_id(intent, attempt=attempt)
        existing = self.store.get_order(cid)
        if existing is not None:
            if existing.status == "open":
                self._log("order_resume", client_order_id=cid)
                return self._settle(existing)
            self._log("order_duplicate_suppressed", client_order_id=cid, status=existing.status,
                      filled=existing.filled)
            return existing if existing.status == "closed" and existing.filled > 0 else None

        info = self.exchange.market_info(self.symbol)
        qty = round_step(intent.qty, info.amount_step)
        price = self.limit_price(intent, info.price_step) if use_limit else None
        notional = qty * (price or intent.ref_price)
        if qty <= 0 or qty < info.min_amount or notional < info.min_notional:
            self._log("order_below_minimum", level=logging.WARNING, client_order_id=cid, qty=qty,
                      notional=round(notional, 2), min_amount=info.min_amount, min_notional=info.min_notional)
            return None

        now = int(self._clock())
        pending = Order(
            client_order_id=cid, symbol=self.symbol, side=intent.side,
            type="limit" if use_limit else "market", price=price, amount=qty,
            status="open", created_at=now, updated_at=now, candle_ts=intent.candle_ts, kind=intent.kind,
            reason=intent.reason,
        )
        self.store.save_order(pending)  # persisted BEFORE the exchange call: a crash mid-flight is recoverable
        try:
            if use_limit:
                assert price is not None
                placed = self.exchange.create_limit_order(self.symbol, intent.side, qty, price, cid)
            else:
                placed = self.exchange.create_market_order(self.symbol, intent.side, qty, cid,
                                                           ref_price=intent.ref_price)
        except Exception as exc:
            # We do not know whether the exchange accepted it. Leave it 'open'; reconcile() on the
            # next start (or the next execute with the same id) resolves it against the exchange.
            self._log("order_submit_error", level=logging.ERROR, client_order_id=cid, error=str(exc))
            raise
        placed.candle_ts, placed.kind, placed.reason, placed.created_at = intent.candle_ts, intent.kind, intent.reason, now
        if placed.price is None:
            placed.price = price
        self.store.save_order(placed)
        return self._settle(placed)

    def _settle(self, order: Order) -> Order | None:
        """Wait for a final status; cancel what has not filled by the timeout."""
        deadline = time.monotonic() + (self.timeout if self.wait_for_fill else 0.0)
        current = order
        while not current.is_final:
            if time.monotonic() >= deadline:
                self._log("order_timeout_cancel", level=logging.WARNING, client_order_id=current.client_order_id,
                          filled=current.filled, amount=current.amount)
                current = self.exchange.cancel_order(current)
                break
            self._sleep(self.poll)
            current = self.exchange.fetch_order(current)
        current.candle_ts, current.kind, current.reason = order.candle_ts, order.kind, order.reason
        if current.status == "open":  # cancel failed to finalise; mark so we never resume it as live
            current.status = "canceled"
        self.store.save_order(current)
        if current.filled < current.amount and current.filled > 0:
            self._log("order_partial_fill", level=logging.WARNING, client_order_id=current.client_order_id,
                      filled=current.filled, amount=current.amount)
        if current.status == "rejected" or current.filled <= 0:
            self._log("order_not_filled", level=logging.WARNING, client_order_id=current.client_order_id,
                      status=current.status, reason=current.reason)
            return None
        self._log("order_filled", client_order_id=current.client_order_id, side=current.side.value,
                  type=current.type, filled=current.filled, avg_price=current.avg_price,
                  fee=round(current.fee, 6), kind=current.kind)
        return current
