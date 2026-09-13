"""TradingEngine: one closed candle in, at most one decision out.

The engine owns the position and wires strategy -> risk -> executor. The same class
runs the live/paper loop and the backtester, so both see identical fills and rules.
Every state change is persisted so a restart resumes exactly where it stopped.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Callable

import pandas as pd

from bot.common import floor_ts, iso, round_step, timeframe_to_ms
from bot.config import BotConfig
from bot.execution.base import ExchangeClient
from bot.execution.executor import OrderExecutor
from bot.execution.order_id import is_bot_order_id
from bot.execution.paper import PaperExchange
from bot.logging_utils import log_event
from bot.models import Order, OrderIntent, Position, Rejection, Side, Trade
from bot.notify.notifier import Notifier
from bot.risk.manager import AccountSnapshot, RiskManager
from bot.state.store import StateStore
from bot.strategy.base import Strategy

log = logging.getLogger(__name__)

KEY_POSITION = "position"
KEY_RISK = "risk_state"
KEY_LAST_CANDLE = "last_candle_ts"
KEY_PAPER_BALANCES = "paper_balances"


class TradingEngine:
    def __init__(
        self,
        *,
        cfg: BotConfig,
        strategy: Strategy,
        risk: RiskManager,
        exchange: ExchangeClient,
        executor: OrderExecutor,
        store: StateStore,
        notifier: Notifier,
        clock: Callable[[], int] | None = None,
        mode: str = "paper",
    ) -> None:
        self.cfg = cfg
        self.symbol = cfg.exchange.symbol
        self.base, self.quote = cfg.exchange.base, cfg.exchange.quote
        self.tf_ms = timeframe_to_ms(cfg.exchange.timeframe)
        self.strategy = strategy
        self.risk = risk
        self.exchange = exchange
        self.executor = executor
        self.store = store
        self.notifier = notifier
        self.mode = mode
        self._clock = clock or exchange.now_ms
        self.position: Position | None = None
        self.last_candle_ts: int | None = None
        self.unmanaged_base = 0.0
        self.trades_closed = 0
        self._load_state()

    # ----- state -------------------------------------------------------------------------
    def _load_state(self) -> None:
        pos = self.store.get_state(KEY_POSITION)
        self.position = Position.from_dict(pos) if pos else None
        self.last_candle_ts = self.store.get_state(KEY_LAST_CANDLE)
        from bot.risk.manager import RiskState

        saved = self.store.get_state(KEY_RISK)
        if saved:
            self.risk.state = RiskState.from_dict(saved)
        if isinstance(self.exchange, PaperExchange):
            bal = self.store.get_state(KEY_PAPER_BALANCES)
            if bal:
                self.exchange.set_balances(bal.get(self.quote, 0.0), bal.get(self.base, 0.0))

    def persist(self) -> None:
        items: list[tuple[str, Any]] = [
            (KEY_POSITION, self.position.to_dict() if self.position else None),
            (KEY_RISK, self.risk.state.to_dict()),
            (KEY_LAST_CANDLE, self.last_candle_ts),
        ]
        if isinstance(self.exchange, PaperExchange):
            items.append((KEY_PAPER_BALANCES, dict(self.exchange.balances)))
        self.store.set_many(items)

    def now(self) -> int:
        return int(self._clock())

    # ----- account -----------------------------------------------------------------------
    def account(self, price: float, now_ms: int | None = None) -> AccountSnapshot:
        bal = self.exchange.fetch_balance()
        cash = float(bal.get(self.quote, {}).get("free", 0.0))
        qty = self.position.qty if self.position else 0.0
        return AccountSnapshot(
            equity=cash + qty * price, cash=cash, price=price,
            now_ms=now_ms if now_ms is not None else self.now(), position=self.position,
        )

    def equity(self, price: float) -> float:
        return self.account(price).equity

    # ----- reconcile ---------------------------------------------------------------------
    def reconcile(self, price: float | None = None) -> dict[str, Any]:
        """Bring local state in line with the exchange before trading. Never places entries."""
        now = self.now()
        price = price if price is not None else self.exchange.fetch_ticker_price(self.symbol)
        summary: dict[str, Any] = {"resolved_orders": 0, "canceled_unknown": 0, "warnings": []}

        # 1. Orders we recorded as open: ask the exchange what happened to them.
        for o in self.store.open_orders():
            try:
                refreshed = self.exchange.fetch_order(o)
            except Exception as exc:  # noqa: BLE001 - exchange never saw it
                refreshed = replace(o, status="canceled", reason=f"not found on exchange at reconcile: {exc}")
            if refreshed.status == "open":
                refreshed = self.exchange.cancel_order(refreshed)
                if refreshed.status == "open":
                    refreshed.status = "canceled"
            refreshed.candle_ts, refreshed.kind, refreshed.reason = o.candle_ts, o.kind, o.reason or refreshed.reason
            self.store.save_order(refreshed)
            summary["resolved_orders"] += 1
            if refreshed.filled > 0:
                intent = OrderIntent(side=refreshed.side, qty=refreshed.filled, ref_price=refreshed.avg_price or price,
                                     kind=(o.kind or ("entry" if refreshed.side == Side.BUY else "exit")),  # type: ignore[arg-type]
                                     candle_ts=o.candle_ts or 0, reason=o.reason)
                self._apply_fill(refreshed, intent, now, price)
                summary["warnings"].append(f"applied fill found at reconcile: {refreshed.client_order_id}")

        # 2. Open orders on the exchange carrying our id that we do not know: cancel them.
        try:
            for o in self.exchange.fetch_open_orders(self.symbol):
                if is_bot_order_id(o.client_order_id) and self.store.get_order(o.client_order_id) is None:
                    self.exchange.cancel_order(o)
                    summary["canceled_unknown"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["warnings"].append(f"could not list open orders: {exc}")

        # 3. Balances vs recorded position.
        bal = self.exchange.fetch_balance()
        base_total = float(bal.get(self.base, {}).get("total", 0.0))
        info = self.exchange.market_info(self.symbol)
        dust = max(info.min_amount, (info.min_notional / price) if price else 0.0)
        if self.position:
            if base_total + 1e-12 < self.position.qty:
                if base_total <= dust:
                    summary["warnings"].append(
                        f"recorded position {self.position.qty} {self.base} but balance is {base_total}; clearing position")
                    self.position = None
                else:
                    summary["warnings"].append(
                        f"recorded position {self.position.qty} {self.base} but balance is {base_total}; shrinking position")
                    self.position.qty = base_total
            else:
                self.unmanaged_base = base_total - self.position.qty
        else:
            self.unmanaged_base = base_total
        if self.unmanaged_base > dust:
            summary["warnings"].append(
                f"{self.unmanaged_base:.8f} {self.base} in the account is not managed by the bot and is ignored")

        self.risk.roll_day(now, self.account(price, now).equity)
        self.persist()
        summary.update(position=self.position.to_dict() if self.position else None,
                       cash=float(bal.get(self.quote, {}).get("free", 0.0)), price=price)
        log_event(log, "reconcile", **summary)
        return summary

    # ----- decisions ---------------------------------------------------------------------
    def process_candle(
        self,
        df: pd.DataFrame,
        *,
        fill_price: float | None = None,
        range_high: float | None = None,
        range_low: float | None = None,
        exit_at_level: bool = False,
    ) -> dict[str, Any]:
        """Evaluate the newest closed candle in df. Returns a summary dict for logging."""
        candle = df.iloc[-1]
        ts = int(candle["ts"])
        now = self.now()
        summary: dict[str, Any] = {"candle_ts": ts, "candle_time": iso(ts), "close": float(candle["close"])}
        if self.last_candle_ts is not None and ts <= self.last_candle_ts:
            summary["skipped"] = f"candle {iso(ts)} already processed"
            return summary

        price = fill_price if fill_price is not None else self.exchange.fetch_ticker_price(self.symbol)
        hi = range_high if range_high is not None else float(candle["high"])
        lo = range_low if range_low is not None else float(candle["low"])
        exit_summary = self.check_protective(
            high=hi, low=lo, candle_ts=ts, now=now, open_price=float(candle["open"]) if exit_at_level else None,
            market_price=None if exit_at_level else price,
        )
        if exit_summary:
            summary["protective"] = exit_summary

        signal = self.evaluate_strategy(df, ts, price)
        account = self.account(price, now)
        decision = self.risk.evaluate(signal, account, ts)
        summary["signal"] = signal.to_dict()
        order: Order | None = None
        if isinstance(decision, OrderIntent):
            summary["decision"] = {"intent": decision.to_dict()}
            order = self.executor.execute(decision)
            if order is not None:
                self._apply_fill(order, decision, now, price)
                summary["order"] = order.to_dict()
        else:
            summary["decision"] = {"rejected": decision.code, "reason": decision.reason}
        for ev in self.risk.state.events:
            log_event(log, "risk_event", level=logging.WARNING, detail=ev)
            self.notifier.halt(ev)
        self.risk.state.events.clear()

        self.last_candle_ts = ts
        acct = self.account(price, now)
        self.store.save_equity(ts, acct.equity, acct.cash, self.position.qty if self.position else 0.0, price)
        self.persist()
        summary.update(
            equity=round(acct.equity, 2), cash=round(acct.cash, 2),
            position=self.position.to_dict() if self.position else None,
            risk=self.risk.status(now, acct.equity),
        )
        return summary

    def evaluate_strategy(self, df: pd.DataFrame, candle_ts: int, price: float):
        """Call the strategy, handing it context when its signature accepts one."""
        import inspect

        try:
            accepts_context = "context" in inspect.signature(self.strategy.on_candle).parameters
        except (TypeError, ValueError):  # pragma: no cover - exotic callables
            accepts_context = False
        if not accepts_context:
            return self.strategy.on_candle(df)
        context = {
            "symbol": self.symbol, "timeframe": self.cfg.exchange.timeframe,
            "candle_time": iso(candle_ts),
            "position": self.position.to_dict() if self.position else None,
            "risk": self.risk.status(self.now(), self.account(price).equity),
        }
        return self.strategy.on_candle(df, context=context)

    def process_tick(self, price: float, now_ms: int | None = None) -> dict[str, Any] | None:
        """Intra-candle stop / take-profit check at the current market price."""
        now = now_ms if now_ms is not None else self.now()
        candle_ts = floor_ts(now, self.tf_ms)
        out = self.check_protective(high=price, low=price, candle_ts=candle_ts, now=now, market_price=price)
        if out:
            self.persist()
        return out

    def check_protective(
        self,
        *,
        high: float,
        low: float,
        candle_ts: int,
        now: int,
        market_price: float | None = None,
        open_price: float | None = None,
    ) -> dict[str, Any] | None:
        decision = self.risk.protective_exit(self.position, high, low, candle_ts)
        if decision is None:
            return None
        if isinstance(decision, Rejection):
            log_event(log, "protective_exit_blocked", level=logging.WARNING, reason=decision.reason)
            return {"blocked": decision.reason}
        intent = decision
        if market_price is not None:
            intent = replace(intent, ref_price=market_price)
        elif open_price is not None:
            # Gap through the level: a stop-market order fills at the open, not at the level.
            if intent.kind == "stop_loss" and open_price < intent.ref_price:
                intent = replace(intent, ref_price=open_price)
            if intent.kind == "take_profit" and open_price > intent.ref_price:
                intent = replace(intent, ref_price=open_price)
        order = self.executor.execute(intent)
        if order is None:
            return {"intent": intent.to_dict(), "order": None}
        self._apply_fill(order, intent, now, intent.ref_price)
        return {"intent": intent.to_dict(), "order": order.to_dict()}

    # ----- manual trading -------------------------------------------------------------------
    def manual_order(
        self,
        side: Side,
        *,
        qty: float | None = None,
        quote_amount: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reason: str = "manual order from the UI",
    ) -> dict[str, Any]:
        """Place an order the operator asked for.

        The kill switch still blocks it and the exchange minimums still apply, but the
        signal gates (confidence, cooldown, daily halt) do not: this is a human decision.
        Sizing is still capped by the cash or coins actually available.
        """
        now = self.now()
        price = self.exchange.fetch_ticker_price(self.symbol)
        if self.risk.kill_switch_active():
            raise PermissionError(f"kill switch is on ({self.cfg.risk.kill_switch_file}); no orders are placed")
        info = self.exchange.market_info(self.symbol)
        bal = self.exchange.fetch_balance()

        if side == Side.BUY:
            if self.position is not None and self.position.qty > 0:
                raise ValueError("already in a position; close it before buying again")
            cash = float(bal.get(self.quote, {}).get("free", 0.0))
            if quote_amount is not None:
                amount = min(float(quote_amount), cash)
            elif qty is not None:
                amount = float(qty) * price
            else:
                raise ValueError("give either qty or quote_amount")
            affordable = cash / (1.0 + self.cfg.exchange.fee_rate)
            amount = min(amount, affordable)
            order_qty = round_step(amount / price, info.amount_step)
            if stop_loss is not None and not (0 < stop_loss < price):
                raise ValueError(f"stop loss {stop_loss} must be above 0 and below the price {price:.2f}")
            if take_profit is not None and take_profit <= price:
                raise ValueError(f"take profit {take_profit} must be above the price {price:.2f}")
        else:
            if self.position is None or self.position.qty <= 0:
                raise ValueError("no open position to sell")
            order_qty = round_step(min(float(qty), self.position.qty) if qty else self.position.qty, info.amount_step)
            stop_loss = take_profit = None

        if order_qty <= 0 or order_qty < info.min_amount or order_qty * price < max(info.min_notional, self.cfg.risk.min_order_notional):
            raise ValueError(
                f"order too small: {order_qty:.8f} {self.base} (~{order_qty * price:.2f} {self.quote}); "
                f"minimum is {max(info.min_notional, self.cfg.risk.min_order_notional):g} {self.quote}")

        intent = OrderIntent(
            side=side, qty=order_qty, ref_price=price,
            kind="entry" if side == Side.BUY else "exit",
            candle_ts=int(now), reason=reason, stop_loss=stop_loss, take_profit=take_profit, confidence=1.0,
        )
        log_event(log, "manual_order", side=side.value, qty=order_qty, price=price,
                  stop_loss=stop_loss, take_profit=take_profit)
        order = self.executor.execute(intent)
        if order is None:
            self.persist()
            raise RuntimeError("the exchange did not fill the order; see the log for the reason")
        self._apply_fill(order, intent, now, price)
        self.persist()
        return {"order": order.to_dict(), "position": self.position.to_dict() if self.position else None,
                "price": price}

    def set_protective_levels(self, stop_loss: float | None, take_profit: float | None) -> dict[str, Any]:
        """Move the stop loss / take profit of the open position."""
        if self.position is None:
            raise ValueError("no open position")
        price = self.exchange.fetch_ticker_price(self.symbol)
        if stop_loss is not None and not (0 < stop_loss < price):
            raise ValueError(f"stop loss must be below the current price {price:.2f}")
        if take_profit is not None and take_profit <= price:
            raise ValueError(f"take profit must be above the current price {price:.2f}")
        self.position.stop_loss = stop_loss
        self.position.take_profit = take_profit
        self.persist()
        log_event(log, "levels_changed", stop_loss=stop_loss, take_profit=take_profit, price=price)
        return self.position.to_dict()

    # ----- fills -------------------------------------------------------------------------
    def _apply_fill(self, order: Order, intent: OrderIntent, now: int, mark_price: float) -> None:
        fill_price = float(order.avg_price or intent.ref_price)
        qty = float(order.filled)
        if order.side == Side.BUY:
            if self.position is not None:
                # Should not happen (risk rejects BUY while long); merge defensively.
                total = self.position.qty + qty
                self.position.entry_price = (self.position.entry_price * self.position.qty + fill_price * qty) / total
                self.position.qty = total
                self.position.fees_paid += order.fee
            else:
                self.position = Position(
                    qty=qty, entry_price=fill_price, entry_ts=order.updated_at or now,
                    stop_loss=intent.stop_loss, take_profit=intent.take_profit,
                    entry_order_id=order.client_order_id, strategy=self.strategy.name, fees_paid=order.fee,
                )
            self.risk.record_entry(now)
            log_event(log, "position_opened", **self.position.to_dict())
            self.notifier.fill(
                f"BUY {qty:.6f} {self.base} @ {fill_price:.2f} stop={intent.stop_loss} tp={intent.take_profit} [{self.mode}]")
            return

        pos = self.position
        if pos is None:
            log_event(log, "sell_without_position", level=logging.ERROR, order=order.to_dict())
            return
        qty = min(qty, pos.qty)
        entry_fee_share = pos.fees_paid * (qty / pos.qty) if pos.qty else 0.0
        fees = entry_fee_share + order.fee
        gross = (fill_price - pos.entry_price) * qty
        pnl = gross - fees
        cost = pos.entry_price * qty
        trade = Trade(
            symbol=self.symbol, strategy=pos.strategy, qty=qty, entry_ts=pos.entry_ts, entry_price=pos.entry_price,
            exit_ts=order.updated_at or now, exit_price=fill_price, fees=fees, pnl=pnl,
            pnl_pct=(pnl / cost * 100.0) if cost else 0.0, exit_reason=intent.kind,
            entry_order_id=pos.entry_order_id, exit_order_id=order.client_order_id,
        )
        self.store.save_trade(trade)
        self.trades_closed += 1
        remaining = pos.qty - qty
        info = self.exchange.market_info(self.symbol)
        if remaining <= max(info.min_amount, 1e-9) or remaining * fill_price < info.min_notional:
            self.position = None
        else:
            pos.qty = remaining
            pos.fees_paid -= entry_fee_share
        equity = self.account(mark_price, now).equity
        events = self.risk.on_trade_closed(trade, equity, now)
        log_event(log, "trade_closed", **trade.to_dict(), equity=round(equity, 2))
        self.notifier.fill(
            f"SELL {qty:.6f} {self.base} @ {fill_price:.2f} ({intent.kind}) pnl={pnl:.2f} {self.quote} [{self.mode}]")
        for ev in events:
            log_event(log, "risk_event", level=logging.WARNING, detail=ev)
            self.notifier.halt(ev)
        self.risk.state.events.clear()
