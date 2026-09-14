"""Performance metrics from an equity curve and a trade list."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from bot.models import Trade

MS_PER_YEAR = 365.25 * 86_400_000


def max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min() * 100.0)


def sharpe_ratio(equity: pd.Series, tf_ms: int, risk_free_annual: float = 0.0) -> float:
    if len(equity) < 3:
        return 0.0
    rets = equity.pct_change().dropna()
    if rets.std(ddof=1) == 0 or np.isnan(rets.std(ddof=1)):
        return 0.0
    periods_per_year = MS_PER_YEAR / tf_ms
    rf_per_period = (1.0 + risk_free_annual) ** (1.0 / periods_per_year) - 1.0
    excess = rets - rf_per_period
    return float(excess.mean() / excess.std(ddof=1) * math.sqrt(periods_per_year))


def _cagr_pct(final: float, initial: float, years: float) -> float | None:
    if final <= 0:
        return -100.0
    try:
        return round(((final / initial) ** (1.0 / years) - 1.0) * 100.0, 2)
    except OverflowError:
        return None


def compute_metrics(
    equity: pd.DataFrame,
    trades: list[Trade],
    *,
    tf_ms: int,
    initial_cash: float,
    first_price: float,
    last_price: float,
) -> dict[str, Any]:
    """equity: DataFrame with columns ts, equity, position_qty (one row per candle)."""
    eq = equity["equity"].astype(float).reset_index(drop=True)
    final = float(eq.iloc[-1]) if len(eq) else initial_cash
    start_ts = int(equity["ts"].iloc[0]) if len(equity) else 0
    end_ts = int(equity["ts"].iloc[-1]) if len(equity) else 0
    years = max((end_ts - start_ts) / MS_PER_YEAR, 1.0 / 365.25)  # floor at one day
    total_return = final / initial_cash - 1.0
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    exposure = float((equity["position_qty"] > 0).mean() * 100.0) if len(equity) else 0.0
    # No losing trades means the profit factor is undefined rather than infinite; JSON has
    # no way to express infinity, and every consumer already handles None as "not available".
    profit_factor: float | None = (gross_profit / gross_loss) if gross_loss > 0 else None
    return {
        "start": start_ts,
        "end": end_ts,
        "days": round((end_ts - start_ts) / 86_400_000, 1),
        "candles": int(len(equity)),
        "initial_equity": round(initial_cash, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round(total_return * 100.0, 2),
        "cagr_pct": _cagr_pct(final, initial_cash, years),
        "buy_hold_return_pct": round((last_price / first_price - 1.0) * 100.0, 2) if first_price else 0.0,
        "max_drawdown_pct": round(max_drawdown_pct(eq), 2),
        "sharpe": round(sharpe_ratio(eq, tf_ms), 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 2) if trades else 0.0,
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "no_losing_trades": gross_loss == 0 and len(trades) > 0,
        # Expectancy is the honest summary: win rate times the average win, minus the loss
        # rate times the average loss. A high win rate with a small average win loses money,
        # which is why tightening the take profit flatters the win rate and empties the
        # account. Equal to avg_trade_pnl by construction; both are kept because people
        # look for the word they know.
        "expectancy": round(sum(t.pnl for t in trades) / len(trades), 2) if trades else 0.0,
        "avg_trade_pnl": round(sum(t.pnl for t in trades) / len(trades), 2) if trades else 0.0,
        "avg_win": round(gross_profit / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "best_trade": round(max((t.pnl for t in trades), default=0.0), 2),
        "worst_trade": round(min((t.pnl for t in trades), default=0.0), 2),
        "total_fees": round(sum(t.fees for t in trades), 2),
        "exposure_pct": round(exposure, 2),
        "exit_reasons": {k: sum(1 for t in trades if t.exit_reason == k) for k in sorted({t.exit_reason for t in trades})},
    }
