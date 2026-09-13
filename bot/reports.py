"""Trade analytics, exports and the daily summary.

Everything here reads the SQLite state written by the engine: nothing is recomputed from
prices, so the numbers are what actually happened, fees included.
"""
from __future__ import annotations

import csv
import io
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from bot.common import iso, now_ms
from bot.models import Trade

log = logging.getLogger(__name__)


def _month(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def monthly_table(trades: Sequence[Trade]) -> list[dict[str, Any]]:
    """Realised profit per calendar month, in the quote currency."""
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        buckets[_month(t.exit_ts)].append(t)
    rows = []
    for month in sorted(buckets):
        group = buckets[month]
        wins = [t for t in group if t.pnl > 0]
        rows.append({
            "month": month, "trades": len(group),
            "pnl": round(sum(t.pnl for t in group), 2),
            "fees": round(sum(t.fees for t in group), 2),
            "win_rate_pct": round(len(wins) / len(group) * 100, 1) if group else 0.0,
            "best": round(max((t.pnl for t in group), default=0.0), 2),
            "worst": round(min((t.pnl for t in group), default=0.0), 2),
        })
    return rows


def streaks(trades: Sequence[Trade]) -> dict[str, int]:
    best = worst = current = 0
    for t in trades:
        if t.pnl > 0:
            current = current + 1 if current > 0 else 1
            best = max(best, current)
        else:
            current = current - 1 if current < 0 else -1
            worst = min(worst, current)
    return {"longest_winning": best, "longest_losing": abs(worst), "current": current}


def analytics(trades: Sequence[Trade], equity: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Everything the Analytics tab shows, computed from closed trades."""
    trades = list(trades)
    if not trades:
        return {"trades": 0, "monthly": [], "streaks": streaks([]), "by_exit": {},
                "durations": {"median_hours": 0.0, "longest_hours": 0.0, "shortest_hours": 0.0},
                "expectancy": 0.0, "pnl": 0.0, "fees": 0.0, "best": 0.0, "worst": 0.0,
                "win_rate_pct": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "first_trade": None, "last_trade": None}
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    durations = [max(0, (t.exit_ts - t.entry_ts) / 3_600_000) for t in trades]
    by_exit: dict[str, dict[str, Any]] = {}
    for reason in sorted({t.exit_reason for t in trades}):
        group = [t for t in trades if t.exit_reason == reason]
        by_exit[reason] = {
            "trades": len(group), "pnl": round(sum(t.pnl for t in group), 2),
            "win_rate_pct": round(len([t for t in group if t.pnl > 0]) / len(group) * 100, 1),
            "avg_pnl": round(sum(t.pnl for t in group) / len(group), 2),
        }
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
    win_rate = len(wins) / len(trades)
    return {
        "trades": len(trades),
        "pnl": round(sum(t.pnl for t in trades), 2),
        "fees": round(sum(t.fees for t in trades), 2),
        "win_rate_pct": round(win_rate * 100, 1),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "best": round(max(t.pnl for t in trades), 2), "worst": round(min(t.pnl for t in trades), 2),
        # What one trade is worth on average, the number that decides whether to keep going.
        "expectancy": round(win_rate * avg_win + (1 - win_rate) * avg_loss, 2),
        "durations": {
            "median_hours": round(statistics.median(durations), 1),
            "longest_hours": round(max(durations), 1),
            "shortest_hours": round(min(durations), 1),
        },
        "monthly": monthly_table(trades),
        "streaks": streaks(trades),
        "by_exit": by_exit,
        "first_trade": iso(min(t.entry_ts for t in trades)),
        "last_trade": iso(max(t.exit_ts for t in trades)),
    }


def trades_csv(trades: Iterable[Trade]) -> str:
    """Closed trades as CSV, ready for a spreadsheet or an accountant."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["entry_time", "exit_time", "symbol", "strategy", "qty", "entry_price", "exit_price",
                     "fees", "pnl", "pnl_pct", "exit_reason", "entry_order_id", "exit_order_id"])
    for t in trades:
        writer.writerow([iso(t.entry_ts), iso(t.exit_ts), t.symbol, t.strategy, f"{t.qty:.8f}",
                         f"{t.entry_price:.2f}", f"{t.exit_price:.2f}", f"{t.fees:.4f}", f"{t.pnl:.4f}",
                         f"{t.pnl_pct:.4f}", t.exit_reason, t.entry_order_id, t.exit_order_id])
    return buf.getvalue()


def equity_csv(rows: Iterable[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["time", "equity", "cash", "position_qty", "price"])
    for r in rows:
        writer.writerow([iso(int(r["ts"])), f"{r['equity']:.2f}", f"{r['cash']:.2f}",
                         f"{r['position_qty']:.8f}", f"{r['price']:.2f}"])
    return buf.getvalue()


def daily_summary(trades: Sequence[Trade], equity: Sequence[dict[str, Any]], *, day: str | None = None,
                  symbol: str = "", mode: str = "") -> str:
    """The message the bot sends once a day. Plain text, readable on a phone."""
    day = day or _day(now_ms())
    todays = [t for t in trades if _day(t.exit_ts) == day]
    curve = [e for e in equity if _day(int(e["ts"])) == day]
    lines = [f"Daily report {day}" + (f" · {symbol}" if symbol else "") + (f" · {mode}" if mode else "")]
    if curve:
        start, end = float(curve[0]["equity"]), float(curve[-1]["equity"])
        change = (end / start - 1) * 100 if start else 0.0
        lines.append(f"Equity {end:,.2f} ({change:+.2f}% today)")
        lines.append(f"Day range {min(float(e['equity']) for e in curve):,.2f} to "
                     f"{max(float(e['equity']) for e in curve):,.2f}")
    if todays:
        won = len([t for t in todays if t.pnl > 0])
        lines.append(f"Trades {len(todays)}, {won} winners, realised {sum(t.pnl for t in todays):+,.2f} "
                     f"(fees {sum(t.fees for t in todays):,.2f})")
        for t in todays[-5:]:
            lines.append(f"  {iso(t.exit_ts)[11:16]} {t.exit_reason} {t.pnl:+,.2f} ({t.pnl_pct:+.2f}%)")
    else:
        lines.append("No trades closed today.")
    if trades:
        overall = sum(t.pnl for t in trades)
        lines.append(f"Since the start: {len(trades)} trades, {overall:+,.2f} realised")
    return "\n".join(lines)
