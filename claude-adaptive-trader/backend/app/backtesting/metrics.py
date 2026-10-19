"""Performance metrics. Undefined values are ``None`` with a note, never a made-up number.

Returns use the equity curve; risk-adjusted ratios use *daily* equity returns (crypto trades
every day, so 365 periods a year). Trade statistics use net PnL after fees and slippage.
"""
from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from app.core.clock import utc_month

TRADE_FIELDS = ["pair", "strategy", "strategy_version", "regime", "environment", "entry_ts", "exit_ts", "entry_price",
                "exit_price", "qty", "gross_pnl", "fees", "slippage_cost", "net_pnl", "return_pct", "initial_risk",
                "r_multiple", "exit_reason", "bars_held", "mae_pct", "mfe_pct"]


def trades_frame(trades: Iterable[Any]) -> pd.DataFrame:
    rows = []
    for t in trades:
        d = asdict(t) if is_dataclass(t) else dict(t)
        rows.append({k: d.get(k) for k in TRADE_FIELDS})
    df = pd.DataFrame(rows, columns=TRADE_FIELDS)
    for col in ("net_pnl", "gross_pnl", "fees", "slippage_cost", "return_pct", "initial_risk", "r_multiple",
                "entry_price", "exit_price", "qty", "bars_held", "mae_pct", "mfe_pct"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _r(x: float | None, digits: int = 4) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), digits)


def group_stats(df: pd.DataFrame) -> dict[str, Any]:
    n = len(df)
    if n == 0:
        return {"trades": 0}
    wins = df[df["net_pnl"] > 0]["net_pnl"].sum()
    losses = df[df["net_pnl"] <= 0]["net_pnl"].sum()
    pf = wins / abs(losses) if losses < 0 else None
    return {
        "trades": int(n),
        "win_rate_pct": _r((df["net_pnl"] > 0).mean() * 100, 2),
        "net_pnl": _r(df["net_pnl"].sum(), 2),
        "expectancy": _r(df["net_pnl"].mean(), 4),
        "expectancy_r": _r(df["r_multiple"].mean(), 4),
        "expectancy_pct": _r(df["return_pct"].mean(), 4),
        "profit_factor": _r(pf, 3),
        "fees": _r(df["fees"].sum(), 2),
    }


def by(df: pd.DataFrame, column: str) -> dict[str, Any]:
    if df.empty:
        return {}
    return {str(k): group_stats(g) for k, g in df.groupby(df[column].fillna("unknown"), sort=True)}


def streaks(pnl: list[float]) -> tuple[int, int]:
    longest_loss = longest_win = cur_loss = cur_win = 0
    for v in pnl:
        if v > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        longest_loss = max(longest_loss, cur_loss)
        longest_win = max(longest_win, cur_win)
    return longest_loss, longest_win


def equity_frame(equity: list[dict[str, Any]]) -> pd.DataFrame:
    if not equity:
        return pd.DataFrame(columns=["ts", "equity", "exposure"])
    df = pd.DataFrame(equity)
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def drawdown(equity: pd.Series) -> tuple[float, int]:
    if equity.empty:
        return 0.0, 0
    peak = equity.cummax()
    dd = 1 - equity / peak
    max_dd = float(dd.max()) * 100
    longest = cur = 0
    for v in (dd > 0).tolist():
        cur = cur + 1 if v else 0
        longest = max(longest, cur)
    return max_dd, longest


def compute_metrics(trades: Iterable[Any], equity: list[dict[str, Any]], *, initial_cash: float,
                    benchmark: list[float] | None = None) -> dict[str, Any]:
    tdf = trades_frame(trades).sort_values("exit_ts") if trades else trades_frame([])
    edf = equity_frame(equity)
    notes: list[str] = []
    final = float(edf["equity"].iloc[-1]) if len(edf) else initial_cash
    n = len(tdf)
    m: dict[str, Any] = {"trades": n, "initial_cash": initial_cash, "final_equity": _r(final, 2)}
    m["net_profit"] = _r(final - initial_cash, 2)
    m["net_return_pct"] = _r((final / initial_cash - 1) * 100, 4)
    fees = float(tdf["fees"].sum()) if n else 0.0
    slip = float(tdf["slippage_cost"].sum()) if n else 0.0
    m["total_fees"] = _r(fees, 2)
    m["slippage_estimate"] = _r(slip, 2)
    m["gross_return_pct"] = _r((final - initial_cash + fees + slip) / initial_cash * 100, 4)
    m["fee_share_of_gross_pct"] = _r(fees / (final - initial_cash + fees + slip) * 100, 2) \
        if (final - initial_cash + fees + slip) > 0 else None

    if n:
        wins, losses = tdf[tdf["net_pnl"] > 0], tdf[tdf["net_pnl"] <= 0]
        m["win_rate_pct"] = _r(len(wins) / n * 100, 2)
        m["avg_win"] = _r(wins["net_pnl"].mean(), 4) if len(wins) else None
        m["avg_loss"] = _r(losses["net_pnl"].mean(), 4) if len(losses) else None
        m["avg_win_r"] = _r(wins["r_multiple"].mean(), 4) if len(wins) else None
        m["avg_loss_r"] = _r(losses["r_multiple"].mean(), 4) if len(losses) else None
        m["expectancy"] = _r(tdf["net_pnl"].mean(), 4)
        m["expectancy_r"] = _r(tdf["r_multiple"].mean(), 4)
        m["expectancy_pct"] = _r(tdf["return_pct"].mean(), 4)
        gw, gl = float(wins["net_pnl"].sum()), float(losses["net_pnl"].sum())
        if gl < 0:
            m["profit_factor"] = _r(gw / abs(gl), 4)
        else:
            m["profit_factor"] = None
            notes.append("no losing trades: profit factor is undefined")
        m["payoff_ratio"] = _r(m["avg_win"] / abs(m["avg_loss"]), 4) if m["avg_win"] and m["avg_loss"] else None
        m["longest_losing_streak"], m["longest_winning_streak"] = streaks(tdf["net_pnl"].tolist())
        m["best_trade"] = _r(tdf["net_pnl"].max(), 2)
        m["worst_trade"] = _r(tdf["net_pnl"].min(), 2)
        total = float(tdf["net_pnl"].sum())
        m["net_without_best_trade"] = _r(total - float(tdf["net_pnl"].max()), 2)
        positive = float(tdf.loc[tdf["net_pnl"] > 0, "net_pnl"].sum())
        m["best_trade_share_pct"] = _r(float(tdf["net_pnl"].max()) / positive * 100, 2) if positive > 0 else None
        m["avg_bars_held"] = _r(tdf["bars_held"].mean(), 2)
        m["avg_mae_pct"] = _r(tdf["mae_pct"].mean(), 3)
        m["avg_mfe_pct"] = _r(tdf["mfe_pct"].mean(), 3)
        notional = float((tdf["entry_price"] * tdf["qty"]).sum() + (tdf["exit_price"] * tdf["qty"]).sum())
        avg_equity = float(edf["equity"].mean()) if len(edf) else initial_cash
        m["turnover"] = _r(notional / avg_equity, 3) if avg_equity else None
    else:
        notes.append("no completed trades: trade statistics are undefined")
        for k in ("win_rate_pct", "avg_win", "avg_loss", "avg_win_r", "avg_loss_r", "expectancy", "expectancy_r",
                  "expectancy_pct", "profit_factor", "payoff_ratio", "best_trade", "worst_trade",
                  "net_without_best_trade", "best_trade_share_pct", "avg_bars_held", "avg_mae_pct", "avg_mfe_pct",
                  "turnover"):
            m[k] = None
        m["longest_losing_streak"] = m["longest_winning_streak"] = 0

    if len(edf):
        max_dd, dd_bars = drawdown(edf["equity"].astype(float))
        m["max_drawdown_pct"] = _r(max_dd, 4)
        m["longest_drawdown_bars"] = dd_bars
        m["exposure_pct"] = _r(float((edf["exposure"] > 0).mean() * 100), 2) if "exposure" in edf else None
        daily = edf.set_index("dt")["equity"].astype(float).resample("1D").last().dropna()
        days = (edf["dt"].iloc[-1] - edf["dt"].iloc[0]).total_seconds() / 86_400
        m["days"] = _r(days, 2)
        rets = daily.pct_change().dropna()
        if len(rets) >= 30 and rets.std(ddof=1) > 0:
            m["sharpe"] = _r(rets.mean() / rets.std(ddof=1) * math.sqrt(365), 3)
            downside = np.sqrt(np.mean(np.minimum(rets.to_numpy(), 0.0) ** 2))
            if downside > 0:
                m["sortino"] = _r(rets.mean() / downside * math.sqrt(365), 3)
            else:
                m["sortino"] = None
                notes.append("no negative days: Sortino ratio is undefined")
        else:
            m["sharpe"] = m["sortino"] = None
            notes.append(f"only {len(rets)} daily returns (fewer than 30) or no variation: Sharpe and Sortino "
                         f"are not meaningful")
        if days >= 30 and final > 0:
            cagr = (final / initial_cash) ** (365 / days) - 1
            m["cagr_pct"] = _r(cagr * 100, 3)
            m["calmar"] = _r(cagr * 100 / max_dd, 3) if max_dd > 0 else None
        else:
            m["cagr_pct"] = m["calmar"] = None
        month = edf.assign(month=edf["ts"].map(utc_month)).groupby("month")["equity"].last()
        prev = initial_cash
        monthly = {}
        for k, v in month.items():
            monthly[str(k)] = _r((float(v) / prev - 1) * 100, 3)
            prev = float(v)
        m["monthly_returns_pct"] = monthly
        if monthly and n:
            pos_months = [v for v in monthly.values() if v and v > 0]
            m["best_month_share_pct"] = (_r(max(pos_months) / sum(pos_months) * 100, 2) if pos_months else None)
        else:
            m["best_month_share_pct"] = None
    else:
        m.update({"max_drawdown_pct": None, "longest_drawdown_bars": 0, "exposure_pct": None, "sharpe": None,
                  "sortino": None, "cagr_pct": None, "calmar": None, "monthly_returns_pct": {}, "days": 0,
                  "best_month_share_pct": None})
    if benchmark:
        bench = float(benchmark[-1])
        m["benchmark_return_pct"] = _r((bench / initial_cash - 1) * 100, 4)
        m["excess_return_pct"] = _r(m["net_return_pct"] - m["benchmark_return_pct"], 4)
        bdd, _ = drawdown(pd.Series(benchmark, dtype=float))
        m["benchmark_max_drawdown_pct"] = _r(bdd, 4)
    else:
        m["benchmark_return_pct"] = m["excess_return_pct"] = m["benchmark_max_drawdown_pct"] = None
    m["by_regime"] = by(tdf, "regime")
    m["by_pair"] = by(tdf, "pair")
    m["by_strategy"] = by(tdf, "strategy")
    m["by_exit_reason"] = by(tdf, "exit_reason")
    m["by_environment"] = by(tdf, "environment")
    m["notes"] = notes
    return m
