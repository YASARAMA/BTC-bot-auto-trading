"""SQLite persistence for orders, trades, the equity curve and key/value bot state."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from bot.models import Order, Side, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    client_order_id   TEXT PRIMARY KEY,
    exchange_order_id TEXT,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,
    type              TEXT NOT NULL,
    price             REAL,
    amount            REAL NOT NULL,
    filled            REAL NOT NULL DEFAULT 0,
    avg_price         REAL,
    fee               REAL NOT NULL DEFAULT 0,
    status            TEXT NOT NULL,
    candle_ts         INTEGER,
    kind              TEXT,
    reason            TEXT,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol         TEXT NOT NULL,
    strategy       TEXT NOT NULL,
    qty            REAL NOT NULL,
    entry_ts       INTEGER NOT NULL,
    entry_price    REAL NOT NULL,
    exit_ts        INTEGER NOT NULL,
    exit_price     REAL NOT NULL,
    fees           REAL NOT NULL,
    pnl            REAL NOT NULL,
    pnl_pct        REAL NOT NULL,
    exit_reason    TEXT NOT NULL,
    entry_order_id TEXT NOT NULL,
    exit_order_id  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity (
    ts           INTEGER PRIMARY KEY,
    equity       REAL NOT NULL,
    cash         REAL NOT NULL,
    position_qty REAL NOT NULL,
    price        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class StateStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a user's database was created.

        CREATE TABLE IF NOT EXISTS leaves an existing table exactly as it was, so a
        database from an older build keeps its old columns until they are added here.
        """
        have = {row["name"] for row in self.conn.execute("PRAGMA table_info(orders)")}
        if "price" not in have:
            self.conn.execute("ALTER TABLE orders ADD COLUMN price REAL")

    def close(self) -> None:
        self.conn.close()

    # ----- orders --------------------------------------------------------------------
    def save_order(self, order: Order) -> None:
        self.conn.execute(
            """
            INSERT INTO orders (client_order_id, exchange_order_id, symbol, side, type, price, amount, filled,
                                avg_price, fee, status, candle_ts, kind, reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                exchange_order_id = excluded.exchange_order_id,
                type = excluded.type, price = excluded.price,
                filled = excluded.filled, avg_price = excluded.avg_price, fee = excluded.fee,
                status = excluded.status, updated_at = excluded.updated_at
            """,
            (
                order.client_order_id, order.exchange_order_id, order.symbol, order.side.value, order.type,
                order.price, order.amount, order.filled, order.avg_price, order.fee, order.status,
                order.candle_ts, order.kind, order.reason, order.created_at, order.updated_at,
            ),
        )
        self.conn.commit()

    def get_order(self, client_order_id: str) -> Order | None:
        row = self.conn.execute("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)).fetchone()
        return self._row_to_order(row) if row else None

    def open_orders(self) -> list[Order]:
        rows = self.conn.execute("SELECT * FROM orders WHERE status = 'open' ORDER BY created_at").fetchall()
        return [self._row_to_order(r) for r in rows]

    def recent_orders(self, limit: int = 50) -> list[Order]:
        rows = self.conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_order(r) for r in rows]

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> Order:
        return Order(
            client_order_id=row["client_order_id"],
            exchange_order_id=row["exchange_order_id"],
            symbol=row["symbol"],
            side=Side(row["side"]),
            type=row["type"],
            price=row["price"] if "price" in row.keys() else None,
            amount=row["amount"],
            filled=row["filled"],
            avg_price=row["avg_price"],
            fee=row["fee"],
            status=row["status"],
            candle_ts=row["candle_ts"],
            kind=row["kind"] or "",
            reason=row["reason"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ----- trades --------------------------------------------------------------------
    def save_trade(self, trade: Trade) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO trades (symbol, strategy, qty, entry_ts, entry_price, exit_ts, exit_price, fees,
                                pnl, pnl_pct, exit_reason, entry_order_id, exit_order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.symbol, trade.strategy, trade.qty, trade.entry_ts, trade.entry_price, trade.exit_ts,
                trade.exit_price, trade.fees, trade.pnl, trade.pnl_pct, trade.exit_reason,
                trade.entry_order_id, trade.exit_order_id,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def trades(self) -> list[Trade]:
        rows = self.conn.execute("SELECT * FROM trades ORDER BY exit_ts, id").fetchall()
        return [
            Trade(
                symbol=r["symbol"], strategy=r["strategy"], qty=r["qty"], entry_ts=r["entry_ts"],
                entry_price=r["entry_price"], exit_ts=r["exit_ts"], exit_price=r["exit_price"],
                fees=r["fees"], pnl=r["pnl"], pnl_pct=r["pnl_pct"], exit_reason=r["exit_reason"],
                entry_order_id=r["entry_order_id"], exit_order_id=r["exit_order_id"],
            )
            for r in rows
        ]

    # ----- equity --------------------------------------------------------------------
    def save_equity(self, ts: int, equity: float, cash: float, position_qty: float, price: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO equity (ts, equity, cash, position_qty, price) VALUES (?, ?, ?, ?, ?)",
            (ts, equity, cash, position_qty, price),
        )
        self.conn.commit()

    def equity_curve(self) -> list[dict[str, float]]:
        rows = self.conn.execute("SELECT * FROM equity ORDER BY ts").fetchall()
        return [dict(r) for r in rows]

    # ----- key/value state -----------------------------------------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, json.dumps(value, default=str))
        )
        self.conn.commit()

    def set_many(self, items: Iterable[tuple[str, Any]]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
            [(k, json.dumps(v, default=str)) for k, v in items],
        )
        self.conn.commit()
