"""Readable Markdown and machine-readable CSV/JSON for every experiment."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.backtesting.metrics import TRADE_FIELDS, trades_frame
from app.core.clock import iso


def _fmt(v: Any, digits: int = 2, suffix: str = "") -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:,.{digits}f}{suffix}"
    return f"{v}{suffix}"


def write_csvs(directory: Path, name: str, trades: list[Any], equity: list[dict[str, Any]],
               benchmark: list[float] | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    df = trades_frame(trades)
    df.insert(0, "exit_time_utc", df["exit_ts"].map(lambda t: iso(int(t)) if t == t and t is not None else ""))
    df.insert(0, "entry_time_utc", df["entry_ts"].map(lambda t: iso(int(t)) if t == t and t is not None else ""))
    df.to_csv(directory / f"{name}_trades.csv", index=False, columns=["entry_time_utc", "exit_time_utc", *TRADE_FIELDS])
    with (directory / f"{name}_equity.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time_utc", "ts", "equity", "cash", "exposure", "open_risk", "drawdown_pct", "benchmark"])
        for j, p in enumerate(equity):
            w.writerow([iso(int(p["ts"])), p["ts"], round(p["equity"], 6), round(p["cash"], 6),
                        round(p.get("exposure", 0.0), 6), round(p.get("open_risk", 0.0), 6),
                        round(p.get("drawdown_pct", 0.0), 6),
                        round(benchmark[j], 6) if benchmark and j < len(benchmark) else ""])


def metrics_table(m: dict[str, Any]) -> list[str]:
    rows = [
        ("Trades", _fmt(m.get("trades"))), ("Net return", _fmt(m.get("net_return_pct"), 2, "%")),
        ("Gross return (before fees and slippage)", _fmt(m.get("gross_return_pct"), 2, "%")),
        ("Total fees", _fmt(m.get("total_fees"))), ("Slippage estimate", _fmt(m.get("slippage_estimate"))),
        ("Win rate", _fmt(m.get("win_rate_pct"), 1, "%")), ("Average win", _fmt(m.get("avg_win"))),
        ("Average loss", _fmt(m.get("avg_loss"))), ("Expectancy per trade", _fmt(m.get("expectancy"))),
        ("Expectancy (R)", _fmt(m.get("expectancy_r"), 3)), ("Profit factor", _fmt(m.get("profit_factor"), 2)),
        ("Max drawdown", _fmt(m.get("max_drawdown_pct"), 2, "%")), ("Sharpe (daily, annualised)", _fmt(m.get("sharpe"), 2)),
        ("Sortino (daily, annualised)", _fmt(m.get("sortino"), 2)), ("Exposure (time in market)", _fmt(m.get("exposure_pct"), 1, "%")),
        ("Turnover (x average equity)", _fmt(m.get("turnover"), 2)),
        ("Longest losing streak", _fmt(m.get("longest_losing_streak"))),
        ("Buy & hold return", _fmt(m.get("benchmark_return_pct"), 2, "%")),
        ("Excess over buy & hold", _fmt(m.get("excess_return_pct"), 2, "%")),
    ]
    out = ["| Metric | Value |", "|---|---:|"]
    out += [f"| {a} | {b} |" for a, b in rows]
    return out


def group_table(title: str, groups: dict[str, Any]) -> list[str]:
    if not groups:
        return [f"### {title}", "", "No trades.", ""]
    out = [f"### {title}", "", "| Group | Trades | Win rate | Expectancy (R) | Net PnL | Profit factor |",
           "|---|---:|---:|---:|---:|---:|"]
    for k, v in groups.items():
        out.append(f"| {k} | {v.get('trades')} | {_fmt(v.get('win_rate_pct'), 1, '%')} | "
                   f"{_fmt(v.get('expectancy_r'), 3)} | {_fmt(v.get('net_pnl'))} | {_fmt(v.get('profit_factor'), 2)} |")
    return out + [""]


def markdown(experiment: dict[str, Any], sections: dict[str, Any]) -> str:
    e = experiment
    lines = [f"# Experiment {e['id']}: {e.get('label') or e['kind']}", "",
             f"*Created {iso(e['created_at'])} UTC by {e['created_by']}. Kind: {e['kind']}. Status: {e['status']}.*", ""]
    if e.get("hypothesis"):
        lines += ["## Hypothesis", "", e["hypothesis"], ""]
    lines += ["## Reproducibility", "", "| Item | Value |", "|---|---|",
              f"| Software version | `{e['software_version']}` |", f"| Configuration hash | `{e['config_hash']}` |",
              f"| Strategies | {', '.join(f'`{s}`' for s in e['strategies'])} |",
              f"| Pairs / timeframe | {', '.join(e['pairs'])} / {e['timeframe']} |",
              f"| Segment | {e['segment']} |",
              f"| Period (UTC candle opens) | {iso(e.get('start_ts'))} to {iso(e.get('end_ts'))} |",
              f"| Dataset | {e.get('dataset_id') or 'ad hoc'} |", f"| Random seed | {e.get('seed')} |",
              f"| Data sources | {json.dumps(e.get('data_sources') or {})} |", ""]
    for title, body in sections.items():
        lines.append(f"## {title}")
        lines.append("")
        if isinstance(body, list):
            lines += body
        else:
            lines.append(str(body))
        lines.append("")
    lines += ["---", "",
              "All results are simulated with the cost assumptions recorded in the configuration snapshot "
              "(fees, slippage, spread and market impact on both sides). Past performance on historical data does "
              "not predict future results. Stops are simulated conservatively but cannot guarantee exit prices.", ""]
    return "\n".join(lines)


def write_markdown(directory: Path, text: str, metrics: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "report.md"
    path.write_text(text, encoding="utf-8")
    (directory / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    return path
