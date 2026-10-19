"""Where the trading core's events go: memory for backtests, SQLite for paper trading."""
from __future__ import annotations

import threading
from typing import Any

from app.execution.models import ClosedTrade, FillResult, Order, Position
from app.storage.database import Database, to_json


class MemoryRecorder:
    """Collects everything a backtest produces; persisted in bulk when the run ends."""

    def __init__(self, keep_decisions: bool = True) -> None:
        self.trades: list[ClosedTrade] = []
        self.equity_points: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.breakers: list[dict[str, Any]] = []
        self.keep_decisions = keep_decisions

    def order(self, order: Order, *, event: str, fill: FillResult | None = None, note: str = "",
              ts: int | None = None) -> None:
        if event in {"rejected", "cancelled"}:
            self.orders.append({"client_order_id": order.client_order_id, "pair": order.pair, "event": event,
                                "note": note, "ts": order.created_ts, "purpose": order.purpose})

    def position_opened(self, pos: Position) -> None:
        pass

    def position_changed(self, pos: Position) -> None:
        pass

    def trade(self, trade: ClosedTrade) -> None:
        self.trades.append(trade)

    def decision(self, row: dict[str, Any]) -> None:
        if self.keep_decisions and row.get("action") in {"enter", "exit"}:
            self.decisions.append(row)

    def equity(self, ts: int, snap: dict[str, Any]) -> None:
        self.equity_points.append({"ts": ts, **snap})

    def breaker(self, event: dict[str, Any]) -> None:
        self.breakers.append(event)


class PaperRecorder:
    """Writes paper-trading events to the database as they happen (one transaction each)."""

    def __init__(self, db: Database, session_id: str, timeframe: str, segment: str, bus: Any = None) -> None:
        self.db = db
        self.session_id = session_id
        self.timeframe = timeframe
        self.segment = segment
        self.bus = bus
        self._lock = threading.Lock()

    def _publish(self, kind: str, data: dict[str, Any]) -> None:
        if self.bus is not None:
            self.bus.publish({"type": "paper", "event": kind, "session_id": self.session_id, "data": data})

    def order(self, order: Order, *, event: str, fill: FillResult | None = None, note: str = "",
              ts: int | None = None) -> None:
        status = {"created": "pending", "waiting": "pending"}.get(event, event)
        fill_ts = ts if fill else None
        with self.db.transaction():
            existing = self.db.query_one("SELECT id FROM orders WHERE client_order_id = ?", (order.client_order_id,))
            if existing is None:
                order.id = self.db.insert(
                    "INSERT INTO orders (session_id, client_order_id, pair, side, purpose, order_type, status,"
                    " requested_qty, filled_qty, ref_price, fill_price, fee, slippage_cost, strategy, strategy_version,"
                    " regime, signal_ts, created_ts, filled_ts, reason, reject_reason, stop_price, take_profit,"
                    " invalidation, time_stop_bars, environment, exit_reason)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.session_id, order.client_order_id, order.pair, order.side, order.purpose,
                     "limit" if "take profit" in order.reason else ("stop" if "stop loss" in order.reason else "market"),
                     status, order.qty, fill.filled_qty if fill else 0.0, order.ref_price,
                     fill.price if fill else None, fill.fee if fill else 0.0, fill.slippage_cost if fill else 0.0,
                     order.strategy, order.strategy_version, order.regime, order.signal_ts, order.created_ts,
                     fill_ts, order.reason,
                     note if event in {"rejected", "cancelled"} else None, order.stop, order.target,
                     order.invalidation, order.time_stop_bars, order.environment, order.exit_reason))
            else:
                self.db.execute(
                    "UPDATE orders SET status = ?, filled_qty = filled_qty + ?, fill_price = COALESCE(?, fill_price),"
                    " fee = fee + ?, slippage_cost = slippage_cost + ?, filled_ts = COALESCE(?, filled_ts),"
                    " reject_reason = COALESCE(?, reject_reason), signal_ts = ?, requested_qty = ?"
                    " WHERE client_order_id = ?",
                    (status, fill.filled_qty if fill else 0.0, fill.price if fill else None,
                     fill.fee if fill else 0.0, fill.slippage_cost if fill else 0.0,
                     fill_ts, note if event in {"rejected", "cancelled"} else None,
                     order.signal_ts, order.qty, order.client_order_id))
        self._publish("order", {"client_order_id": order.client_order_id, "pair": order.pair, "status": status,
                                "purpose": order.purpose, "note": note})

    def position_opened(self, pos: Position) -> None:
        pos.id = self.db.insert(
            "INSERT INTO positions (session_id, pair, status, qty, entry_price, entry_ts, entry_fee, entry_slippage,"
            " stop_price, take_profit, initial_risk, strategy, strategy_version, regime, environment, entry_reason,"
            " signal_ts, time_stop_bars, bars_held, high_water, low_water, invalidation, position_key)"
            " VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.session_id, pos.pair, pos.qty, pos.entry_price, pos.entry_ts, pos.entry_fee, pos.entry_slippage,
             pos.stop, pos.target, pos.initial_risk, pos.strategy, pos.strategy_version, pos.regime, pos.environment,
             pos.entry_reason, pos.signal_ts, pos.time_stop_bars, pos.bars_held, pos.high_water, pos.low_water,
             pos.invalidation, pos.key))
        self._publish("position_opened", {"pair": pos.pair, "qty": pos.qty, "entry_price": pos.entry_price})

    def position_changed(self, pos: Position) -> None:
        self.db.execute(
            "UPDATE positions SET bars_held = ?, high_water = ?, low_water = ?, qty_exited = ?, exit_value = ?,"
            " exit_fees = ?, exit_slippage = ? WHERE session_id = ? AND position_key = ? AND status = 'open'",
            (pos.bars_held, pos.high_water, pos.low_water, pos.exit_qty, pos.exit_value, pos.exit_fees,
             pos.exit_slippage, self.session_id, pos.key))

    def trade(self, trade: ClosedTrade) -> None:
        with self.db.transaction():
            self.db.execute(
                "UPDATE positions SET status = 'closed', closed_ts = ?, exit_reason = ?, qty_exited = qty"
                " WHERE session_id = ? AND position_key = ? AND status = 'open'",
                (trade.exit_ts, trade.exit_reason, self.session_id, trade.position_key))
            self.db.execute(
                "INSERT OR IGNORE INTO trades (source, run_id, scenario, segment, position_key, pair, timeframe,"
                " strategy, strategy_version, regime, environment, entry_ts, exit_ts, entry_price, exit_price, qty,"
                " stop_price, take_profit, gross_pnl, fees, slippage_cost, net_pnl, return_pct, initial_risk,"
                " r_multiple, exit_reason, entry_reason, mae_pct, mfe_pct, bars_held)"
                " VALUES ('paper', ?, 'base', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.session_id, self.segment, trade.position_key, trade.pair, self.timeframe, trade.strategy,
                 trade.strategy_version, trade.regime, trade.environment, trade.entry_ts, trade.exit_ts,
                 trade.entry_price, trade.exit_price, trade.qty, trade.stop_price, trade.take_profit, trade.gross_pnl,
                 trade.fees, trade.slippage_cost, trade.net_pnl, trade.return_pct, trade.initial_risk,
                 trade.r_multiple, trade.exit_reason, trade.entry_reason, trade.mae_pct, trade.mfe_pct,
                 trade.bars_held))
        self._publish("trade", {"pair": trade.pair, "net_pnl": trade.net_pnl, "exit_reason": trade.exit_reason})

    def decision(self, row: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO decisions (session_id, ts, pair, timeframe, regime, regime_confidence, strategy,"
            " strategy_version, action, accepted, reason, checks, signal) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.session_id, row["ts"], row["pair"], self.timeframe, row.get("regime", ""),
             row.get("regime_confidence"), row.get("strategy"), row.get("strategy_version"), row["action"],
             1 if row.get("accepted") else 0, row.get("reason", ""),
             to_json(row.get("checks")) if row.get("checks") else None,
             to_json({**(row.get("signal") or {}), "scores": row.get("scores")}) if row.get("signal") or row.get("scores")
             else None))
        if row["action"] in {"enter", "exit"}:
            self._publish("decision", {"pair": row["pair"], "action": row["action"], "accepted": row.get("accepted"),
                                       "reason": row.get("reason")})

    def equity(self, ts: int, snap: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO equity_curve (source, run_id, scenario, ts, equity, cash, exposure, open_risk, drawdown_pct)"
            " VALUES ('paper', ?, 'base', ?, ?, ?, ?, ?, ?) ON CONFLICT(source, run_id, scenario, ts) DO UPDATE SET"
            " equity = excluded.equity, cash = excluded.cash, exposure = excluded.exposure,"
            " open_risk = excluded.open_risk, drawdown_pct = excluded.drawdown_pct",
            (self.session_id, ts, snap["equity"], snap["cash"], snap["exposure"], snap["open_risk"],
             snap.get("drawdown_pct", 0.0)))
        self._publish("equity", {"ts": ts, "equity": snap["equity"]})

    def breaker(self, event: dict[str, Any]) -> None:
        from app.monitoring.logging import get_logger, log_event

        log_event(get_logger("risk"), 30 if "tripped" in event["event"] else 20,
                  f"circuit breaker: {event['detail']}", session=self.session_id, **{"event": event["event"]})
        self._publish("breaker", event)
