"""Local candle cache, keyed by exchange, pair and timeframe.

Every stored candle carries the label of where it came from - ``binance-rest``,
``binance-ws``, ``local-file:<name>`` - so a chart or a report can always say what it is
showing. Data from another exchange is stored under that exchange's name and is never
presented as Binance data.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.core.clock import now_ms
from app.core.timeframes import tf_ms
from app.data.validation import CANDLE_COLUMNS, find_gaps
from app.storage.database import Database, from_json, to_json

_BATCH = 5000


class CandleStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ----- writes ------------------------------------------------------------------------
    def upsert(self, exchange: str, pair: str, timeframe: str, df: pd.DataFrame, source: str) -> int:
        """Insert or replace candles. ``df`` must already be validated (closed candles only)."""
        if df.empty:
            return 0
        ingested = now_ms()
        rows = [
            (exchange, pair, timeframe, int(ts), float(o), float(h), float(lo), float(c), float(v), source, ingested)
            for ts, o, h, lo, c, v in df[CANDLE_COLUMNS].itertuples(index=False, name=None)
        ]
        sql = ("INSERT INTO candles (exchange, pair, timeframe, ts, open, high, low, close, volume, source,"
               " ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
               " ON CONFLICT(exchange, pair, timeframe, ts) DO UPDATE SET open = excluded.open,"
               " high = excluded.high, low = excluded.low, close = excluded.close, volume = excluded.volume,"
               " source = excluded.source, ingested_at = excluded.ingested_at")
        with self.db.transaction():
            for i in range(0, len(rows), _BATCH):
                self.db.raw().executemany(sql, rows[i:i + _BATCH])
        return len(rows)

    def delete_series(self, exchange: str, pair: str, timeframe: str) -> int:
        with self.db.transaction():
            n = self.db.execute("DELETE FROM candles WHERE exchange = ? AND pair = ? AND timeframe = ?",
                                (exchange, pair, timeframe)).rowcount
            self.db.execute("DELETE FROM series_meta WHERE exchange = ? AND pair = ? AND timeframe = ?",
                            (exchange, pair, timeframe))
        return int(n or 0)

    # ----- reads -------------------------------------------------------------------------
    def load(self, exchange: str, pair: str, timeframe: str, *, start: int | None = None, end: int | None = None,
             limit: int | None = None, newest: bool = False, with_source: bool = False) -> pd.DataFrame:
        """Candles by open time, ``start`` and ``end`` inclusive, oldest first.

        ``newest=True`` with ``limit`` returns the most recent ``limit`` candles (still oldest first).
        """
        cols = ", ".join(CANDLE_COLUMNS + (["source"] if with_source else []))
        where = ["exchange = ?", "pair = ?", "timeframe = ?"]
        params: list[Any] = [exchange, pair, timeframe]
        if start is not None:
            where.append("ts >= ?")
            params.append(int(start))
        if end is not None:
            where.append("ts <= ?")
            params.append(int(end))
        order = "DESC" if newest else "ASC"
        sql = f"SELECT {cols} FROM candles WHERE {' AND '.join(where)} ORDER BY ts {order}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self.db.lock:
            df = pd.read_sql_query(sql, self.db.raw(), params=params)
        if newest:
            df = df.iloc[::-1].reset_index(drop=True)
        if df.empty:
            return pd.DataFrame(columns=CANDLE_COLUMNS + (["source"] if with_source else []))
        df["ts"] = df["ts"].astype("int64")
        return df

    def latest_ts(self, exchange: str, pair: str, timeframe: str) -> int | None:
        v = self.db.scalar("SELECT MAX(ts) FROM candles WHERE exchange = ? AND pair = ? AND timeframe = ?",
                           (exchange, pair, timeframe))
        return int(v) if v is not None else None

    def first_ts(self, exchange: str, pair: str, timeframe: str) -> int | None:
        v = self.db.scalar("SELECT MIN(ts) FROM candles WHERE exchange = ? AND pair = ? AND timeframe = ?",
                           (exchange, pair, timeframe))
        return int(v) if v is not None else None

    def count(self, exchange: str, pair: str, timeframe: str) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM candles WHERE exchange = ? AND pair = ? AND timeframe = ?",
                                  (exchange, pair, timeframe)) or 0)

    def sources(self, exchange: str, pair: str, timeframe: str, start: int | None = None,
                end: int | None = None) -> dict[str, int]:
        where, params = "exchange = ? AND pair = ? AND timeframe = ?", [exchange, pair, timeframe]
        if start is not None:
            where += " AND ts >= ?"
            params.append(start)
        if end is not None:
            where += " AND ts <= ?"
            params.append(end)
        rows = self.db.query(f"SELECT source, COUNT(*) AS n FROM candles WHERE {where} GROUP BY source", params)
        return {r["source"]: int(r["n"]) for r in rows}

    def gaps(self, exchange: str, pair: str, timeframe: str, start: int | None = None,
             end: int | None = None) -> tuple[int, list[tuple[int, int]]]:
        where, params = "exchange = ? AND pair = ? AND timeframe = ?", [exchange, pair, timeframe]
        if start is not None:
            where += " AND ts >= ?"
            params.append(start)
        if end is not None:
            where += " AND ts <= ?"
            params.append(end)
        with self.db.lock:
            ts = np.array([r[0] for r in self.db.raw().execute(
                f"SELECT ts FROM candles WHERE {where} ORDER BY ts", params).fetchall()], dtype="int64")
        return find_gaps(ts, tf_ms(timeframe))

    def series(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT exchange, pair, timeframe, COUNT(*) AS candles, MIN(ts) AS first_ts, MAX(ts) AS last_ts"
            " FROM candles GROUP BY exchange, pair, timeframe ORDER BY exchange, pair, timeframe")
        return rows

    def inventory(self) -> list[dict[str, Any]]:
        meta = {(m["exchange"], m["pair"], m["timeframe"]): m for m in self.db.query("SELECT * FROM series_meta")}
        out = []
        for row in self.series():
            key = (row["exchange"], row["pair"], row["timeframe"])
            step = tf_ms(row["timeframe"])
            expected = (row["last_ts"] - row["first_ts"]) // step + 1 if row["candles"] else 0
            missing, gaps = self.gaps(*key)
            m = meta.get(key, {})
            out.append({
                **row,
                "expected": int(expected),
                "missing": missing,
                "gap_count": len(gaps),
                "coverage_pct": round(row["candles"] / expected * 100, 3) if expected else 0.0,
                "sources": self.sources(*key),
                "last_sync_at": m.get("last_sync_at"),
                "last_sync_status": m.get("last_sync_status"),
                "last_error": m.get("last_error"),
            })
        return out

    # ----- metadata ----------------------------------------------------------------------
    def set_meta(self, exchange: str, pair: str, timeframe: str, *, status: str, error: str | None = None,
                 report: dict[str, Any] | None = None) -> None:
        self.db.execute(
            "INSERT INTO series_meta (exchange, pair, timeframe, last_sync_at, last_sync_status, last_error,"
            " last_report) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(exchange, pair, timeframe) DO UPDATE SET"
            " last_sync_at = excluded.last_sync_at, last_sync_status = excluded.last_sync_status,"
            " last_error = excluded.last_error, last_report = excluded.last_report",
            (exchange, pair, timeframe, now_ms(), status, error, to_json(report) if report else None))

    def meta(self, exchange: str, pair: str, timeframe: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM series_meta WHERE exchange = ? AND pair = ? AND timeframe = ?",
                                (exchange, pair, timeframe))
        if row and row.get("last_report"):
            row["last_report"] = from_json(row["last_report"])
        return row

    def save_rules(self, exchange: str, pair: str, rules: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO market_rules (exchange, pair, payload, fetched_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(exchange, pair) DO UPDATE SET payload = excluded.payload, fetched_at = excluded.fetched_at",
            (exchange, pair, to_json(rules), now_ms()))

    def load_rules(self, exchange: str, pair: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT payload, fetched_at FROM market_rules WHERE exchange = ? AND pair = ?",
                                (exchange, pair))
        if not row:
            return None
        rules = from_json(row["payload"], {})
        rules["fetched_at"] = row["fetched_at"]
        return rules
