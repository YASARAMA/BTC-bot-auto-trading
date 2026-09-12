"""OrderExecutor: the only code path that places orders.

Responsibilities: idempotent ids, exchange precision/minimums, persisting every order
before and after it hits the exchange, waiting for fills, cancelling leftovers, and
returning what actually filled (which may be less than requested)."""
from __future__ import annotations

import logging
import time
from typing import Callable

from bot.common import round_step
from bot.execution.base import ExchangeClient
from bot.execution.order_id import make_client_order_id
from bot.logging_utils import log_event
from bot.models import Order, OrderIntent
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
    ) -> None:
        self.exchange = exchange
        self.store = store
        self.symbol = symbol
        self.strategy_name = strategy_name
        self.timeout = order_timeout_seconds
        self.poll = order_poll_seconds
        self._sleep = sleep
        self._clock = clock or exchange.now_ms

    def client_order_id(self, intent: OrderIntent) -> str:
        return make_client_order_id(self.strategy_name, self.symbol, intent.candle_ts, intent.side.value)

    def execute(self, intent: OrderIntent) -> Order | None:
        cid = self.client_order_id(intent)
        existing = self.store.get_order(cid)
        if existing is not None:
            if existing.status == "open":
                log_event(log, "order_resume", client_order_id=cid)
                return self._settle(existing)
            log_event(log, "order_duplicate_suppressed", client_order_id=cid, status=existing.status,
                      filled=existing.filled)
            return existing if existing.status == "closed" and existing.filled > 0 else None

        info = self.exchange.market_info(self.symbol)
        qty = round_step(intent.qty, info.amount_step)
        notional = qty * intent.ref_price
        if qty <= 0 or qty < info.min_amount or notional < info.min_notional:
            log_event(log, "order_below_minimum", level=logging.WARNING, client_order_id=cid, qty=qty,
                      notional=round(notional, 2), min_amount=info.min_amount, min_notional=info.min_notional)
            return None

        now = int(self._clock())
        pending = Order(
            client_order_id=cid, symbol=self.symbol, side=intent.side, type="market", amount=qty,
            status="open", created_at=now, updated_at=now, candle_ts=intent.candle_ts, kind=intent.kind,
            reason=intent.reason,
        )
        self.store.save_order(pending)  # persisted BEFORE the exchange call: a crash mid-flight is recoverable
        try:
            placed = self.exchange.create_market_order(self.symbol, intent.side, qty, cid, ref_price=intent.ref_price)
        except Exception as exc:
            # We do not know whether the exchange accepted it. Leave it 'open'; reconcile() on the
            # next start (or the next execute with the same id) resolves it against the exchange.
            log_event(log, "order_submit_error", level=logging.ERROR, client_order_id=cid, error=str(exc))
            raise
        placed.candle_ts, placed.kind, placed.reason, placed.created_at = intent.candle_ts, intent.kind, intent.reason, now
        self.store.save_order(placed)
        return self._settle(placed)

    def _settle(self, order: Order) -> Order | None:
        """Wait for a final status; cancel what has not filled by the timeout."""
        deadline = time.monotonic() + self.timeout
        current = order
        while not current.is_final:
            if time.monotonic() >= deadline:
                log_event(log, "order_timeout_cancel", level=logging.WARNING, client_order_id=current.client_order_id,
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
            log_event(log, "order_partial_fill", level=logging.WARNING, client_order_id=current.client_order_id,
                      filled=current.filled, amount=current.amount)
        if current.status == "rejected" or current.filled <= 0:
            log_event(log, "order_not_filled", level=logging.WARNING, client_order_id=current.client_order_id,
                      status=current.status, reason=current.reason)
            return None
        log_event(log, "order_filled", client_order_id=current.client_order_id, side=current.side.value,
                  filled=current.filled, avg_price=current.avg_price, fee=round(current.fee, 6), kind=current.kind)
        return current
