"""CLI: python -m bot.backtest --strategy ema_rsi --from 2023-01-01 --to 2024-12-31

Data comes from the configured exchange through ccxt (cached under --cache-dir), or
from a local CSV with --csv when offline."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from bot.backtest.engine import BacktestResult, run_backtest
from bot.common import iso, parse_date_ms, timeframe_to_ms
from bot.config import load_config
from bot.data.history import cache_path, download_ohlcv, load_csv
from bot.logging_utils import setup_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m bot.backtest", description=__doc__)
    p.add_argument("--strategy", help="strategy name (default: config.yaml)")
    p.add_argument("--from", dest="date_from", help="start date, e.g. 2023-01-01 (UTC)")
    p.add_argument("--to", dest="date_to", help="end date, e.g. 2024-12-31 (UTC, inclusive)")
    p.add_argument("--csv", help="load candles from this CSV instead of downloading")
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument("--exchange", help="override exchange id for download")
    p.add_argument("--symbol", help="override symbol")
    p.add_argument("--timeframe", help="override timeframe")
    p.add_argument("--cash", type=float, help="initial cash (default: paper.initial_cash)")
    p.add_argument("--cache-dir", default="data/history", help="CSV cache for downloaded candles")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="override a strategy param")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.add_argument("--show-trades", type=int, default=10, metavar="N", help="print the last N trades (0 = none)")
    p.add_argument("--verbose", action="store_true", help="show engine logs while running")
    return p.parse_args(argv)


def _coerce(value: str) -> Any:
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def load_candles(args: argparse.Namespace, cfg: Any, since: int | None, until: int | None):
    if args.csv:
        df = load_csv(args.csv)
    else:
        from bot.execution.live import LiveExchange

        if since is None or until is None:
            raise SystemExit("--from and --to are required when downloading (or pass --csv)")
        market = LiveExchange(cfg.exchange)
        cache = cache_path(args.cache_dir, cfg.exchange.id, cfg.exchange.symbol, cfg.exchange.timeframe)
        try:
            df = download_ohlcv(market, symbol=cfg.exchange.symbol, timeframe=cfg.exchange.timeframe,
                                since_ms=since, until_ms=until, cache=cache)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                f"download from {cfg.exchange.id} failed ({type(exc).__name__}: {exc}). "
                f"If you are offline, pass --csv <file> (see data/samples/)."
            ) from exc
    if since is not None:
        df = df[df["ts"] >= since]
    if until is not None:
        df = df[df["ts"] <= until]
    return df.reset_index(drop=True)


def format_report(result: BacktestResult, show_trades: int) -> str:
    m = result.metrics
    lines = [
        "=" * 64,
        f"Backtest  {result.config['symbol']}  {result.config['timeframe']}  strategy={result.strategy['name']}",
        f"Period    {iso(m['start'])} -> {iso(m['end'])}  ({m['days']} days, {m['candles']} candles)",
        f"Costs     fee={result.config['fee_rate'] * 100:.3f}%  slippage={result.config['slippage_bps']:g} bps",
        "-" * 64,
        f"{'Initial equity':<24}{m['initial_equity']:>16,.2f}",
        f"{'Final equity':<24}{m['final_equity']:>16,.2f}",
        f"{'Total return':<24}{m['total_return_pct']:>15.2f}%",
        f"{'CAGR':<24}{m['cagr_pct']:>15.2f}%",
        f"{'Buy & hold return':<24}{m['buy_hold_return_pct']:>15.2f}%",
        f"{'Max drawdown':<24}{m['max_drawdown_pct']:>15.2f}%",
        f"{'Sharpe (annualised)':<24}{m['sharpe']:>16.2f}",
        f"{'Trades':<24}{m['trades']:>16d}",
        f"{'Win rate':<24}{m['win_rate_pct']:>15.2f}%",
        f"{'Profit factor':<24}{str(m['profit_factor']) if m['profit_factor'] is not None else 'n/a':>16}",
        f"{'Avg trade PnL':<24}{m['avg_trade_pnl']:>16,.2f}",
        f"{'Avg win / avg loss':<24}{m['avg_win']:>8,.2f} / {m['avg_loss']:>6,.2f}",
        f"{'Best / worst trade':<24}{m['best_trade']:>8,.2f} / {m['worst_trade']:>6,.2f}",
        f"{'Total fees':<24}{m['total_fees']:>16,.2f}",
        f"{'Exposure':<24}{m['exposure_pct']:>15.2f}%",
        f"{'Exits':<24}{json.dumps(m['exit_reasons']):>16}",
        "-" * 64,
    ]
    if show_trades and result.trades:
        lines.append(f"Last {min(show_trades, len(result.trades))} trades:")
        lines.append(f"  {'entry':<20} {'exit':<20} {'qty':>10} {'entry px':>10} {'exit px':>10} {'pnl':>9} {'reason'}")
        for t in result.trades[-show_trades:]:
            lines.append(
                f"  {iso(t.entry_ts):<20} {iso(t.exit_ts):<20} {t.qty:>10.5f} {t.entry_price:>10.2f} "
                f"{t.exit_price:>10.2f} {t.pnl:>9.2f} {t.exit_reason}"
            )
        lines.append("-" * 64)
    for note in result.notes:
        lines.append(f"NOTE      {note}")
    lines.append("Backtest results are hypothetical. Past performance does not predict future returns.")
    lines.append("=" * 64)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config)
    setup_logging("INFO" if args.verbose else "WARNING", "text")
    updates: dict[str, Any] = {}
    if args.exchange:
        updates["id"] = args.exchange
    if args.symbol:
        updates["symbol"] = args.symbol
    if args.timeframe:
        updates["timeframe"] = args.timeframe
    if updates:
        cfg = cfg.model_copy(update={"exchange": cfg.exchange.model_copy(update=updates)})

    since = parse_date_ms(args.date_from) if args.date_from else None
    until = parse_date_ms(args.date_to) + 86_400_000 - 1 if args.date_to else None  # inclusive day
    df = load_candles(args, cfg, since, until)
    if df.empty:
        raise SystemExit("no candles in the requested range")

    params = dict(cfg.strategy.params)
    for item in args.param:
        if "=" not in item:
            raise SystemExit(f"--param expects KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        params[k] = _coerce(v)

    print(f"Loaded {len(df)} candles {iso(int(df['ts'].iloc[0]))} -> {iso(int(df['ts'].iloc[-1]))} "
          f"({cfg.exchange.symbol} {cfg.exchange.timeframe}, step {timeframe_to_ms(cfg.exchange.timeframe) // 60000} min)",
          file=sys.stderr)
    result = run_backtest(df, cfg, strategy_name=args.strategy, params=params, initial_cash=args.cash)
    if args.json:
        print(json.dumps({"metrics": result.metrics, "strategy": result.strategy, "config": result.config,
                          "trades": [t.to_dict() for t in result.trades]}, indent=2, default=str))
    else:
        print(format_report(result, args.show_trades))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
