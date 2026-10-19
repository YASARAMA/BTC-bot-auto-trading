"""One command that answers the only question that matters: should this be traded?

    python -m bot.validate --csv data/samples/binance_BTCUSDT_1h_2020-11_2021-05.csv
    python -m bot.validate --symbol BTC/USDT --from 2021-01-01 --to 2024-12-31

A backtest is a story the past tells about itself. This runs the three checks that can
contradict it, on data the tuning never saw, and ends with a verdict rather than a number:

1. Walk-forward: optimise on one block of history, trade those settings on the next block.
   Only the out-of-sample result counts.
2. Monte Carlo: re-deal those trades thousands of times. A strategy that loses in a third
   of the orderings is a coin flip, whatever the single run says.
3. Sensitivity: nudge each setting. Profit that exists only at one exact value was fitted
   to this history and will not repeat.

It refuses to say yes on thin evidence, and the bar is deliberately high: the cost of a
false yes is real money, the cost of a false no is a missed backtest.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from bot.backtest.engine import run_backtest
from bot.backtest.optimize import DEFAULT_OBJECTIVE, walk_forward
from bot.backtest.robustness import monte_carlo, sensitivity
from bot.common import iso
from bot.config import BotConfig

log = logging.getLogger(__name__)

# What a strategy has to clear before this says yes. Each line is a way of being wrong that
# has emptied an account before.
MIN_TRADES = 30                  # fewer than this and the statistics mean nothing
MIN_OUT_OF_SAMPLE_PCT = 0.0      # out of sample must at least not lose
MAX_PROB_LOSS_PCT = 35.0         # how often a re-deal may end down
MAX_PROB_RUIN_PCT = 1.0          # ...and how often it may halve the account
MIN_PROFITABLE_FOLD_RATIO = 0.5  # it has to work in more than half the blocks
MAX_SPIKE_RATIO = 0.34           # at most a third of the settings may be lone peaks


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    measured: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "measured": self.measured}


@dataclass
class Validation:
    symbol: str
    timeframe: str
    strategy: str
    candles: int
    period: tuple[str | None, str | None]
    checks: list[Check] = field(default_factory=list)
    walk_forward: dict[str, Any] = field(default_factory=dict)
    monte_carlo: dict[str, Any] = field(default_factory=dict)
    sensitivity: dict[str, Any] = field(default_factory=dict)
    in_sample: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "timeframe": self.timeframe, "strategy": self.strategy,
            "candles": self.candles, "from": self.period[0], "to": self.period[1],
            "passed": self.passed, "verdict": self.verdict,
            "checks": [c.to_dict() for c in self.checks],
            "walk_forward": self.walk_forward, "monte_carlo": self.monte_carlo,
            "sensitivity": self.sensitivity, "in_sample": self.in_sample,
        }

    def report(self) -> str:
        width = 72
        head = f"Validation  {self.symbol}  {self.timeframe}  strategy={self.strategy}"
        lines = ["=" * width, head,
                 f"Period      {self.period[0]} -> {self.period[1]}  ({self.candles:,} candles)",
                 "-" * width]
        for check in self.checks:
            mark = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{mark}] {check.name:<34} {check.detail}")
        lines += ["-" * width, "VERDICT" if self.passed else "VERDICT (do not trade this)", ""]
        lines += [f"  {line}" for line in _wrap(self.verdict, width - 4)]
        lines += ["", "Backtest results are hypothetical. Past performance does not predict future returns.",
                  "=" * width]
        return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            if len(line) + len(word) + 1 > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        out.append(line)
    return out


def validate(
    df: pd.DataFrame,
    cfg: BotConfig,
    *,
    strategy_name: str | None = None,
    folds: int = 4,
    sample: int | None = 60,
    objective: str = DEFAULT_OBJECTIVE,
    runs: int = 2000,
    workers: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> Validation:
    """Run the three checks and decide. Raises only when the data cannot carry the test."""
    name = strategy_name or cfg.strategy.name
    note = progress or (lambda _text: None)
    out = Validation(
        symbol=cfg.exchange.symbol, timeframe=cfg.exchange.timeframe, strategy=name, candles=int(len(df)),
        period=(iso(int(df["ts"].iloc[0])), iso(int(df["ts"].iloc[-1]))) if len(df) else (None, None),
    )

    note("backtesting the current settings")
    plain = run_backtest(df, cfg, strategy_name=name)
    out.in_sample = plain.metrics

    note(f"walk-forward over {folds} blocks")
    wf = walk_forward(df, cfg, folds=folds, strategy_name=name, objective=objective, sample=sample,
                      workers=workers)
    out.walk_forward = wf
    oos = wf.get("out_of_sample") or {}
    stability = wf.get("stability") or {}

    note("re-dealing the out-of-sample trades")
    trades = wf.get("trades") or []
    equity = pd.DataFrame(wf.get("equity") or [])
    mc: dict[str, Any] = {}
    if len(equity) and trades:
        try:
            mc = monte_carlo(_as_trades(trades), equity, runs=runs).to_dict()
        except ValueError as exc:  # too few trades to re-deal
            mc = {"error": str(exc)}
    out.monte_carlo = mc

    note("sweeping each setting")
    try:
        sens = sensitivity(df, cfg, strategy_name=name, objective=objective, workers=workers).to_dict()
    except ValueError as exc:
        sens = {"error": str(exc)}
    out.sensitivity = sens

    out.checks = _checks(oos, stability, mc, sens)
    out.passed = all(c.passed for c in out.checks)
    out.verdict = _verdict(out)
    return out


def _as_trades(rows: list[dict[str, Any]]) -> list[Any]:
    from bot.models import Trade

    known = set(Trade.__dataclass_fields__)
    return [Trade(**{k: v for k, v in row.items() if k in known}) for row in rows]


def _checks(oos: dict[str, Any], stability: dict[str, Any], mc: dict[str, Any],
            sens: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []

    trades = int(oos.get("trades", 0) or 0)
    checks.append(Check(
        "enough out-of-sample trades", trades >= MIN_TRADES,
        f"{trades} trades out of sample (needs {MIN_TRADES}+ before the numbers mean anything)", trades))

    ret = float(oos.get("total_return_pct", 0.0) or 0.0)
    checks.append(Check(
        "made money out of sample", ret > MIN_OUT_OF_SAMPLE_PCT,
        f"{ret:+.2f}% on blocks the tuning never saw", ret))

    hold = float(oos.get("buy_hold_return_pct", 0.0) or 0.0)
    checks.append(Check(
        "beat buying and holding", ret > hold,
        f"{ret:+.2f}% against {hold:+.2f}% for holding the same period", {"strategy": ret, "buy_hold": hold}))

    folds = int(stability.get("folds", 0) or 0)
    good = int(stability.get("profitable_folds", 0) or 0)
    ratio = good / folds if folds else 0.0
    checks.append(Check(
        "worked in most blocks", folds > 0 and ratio > MIN_PROFITABLE_FOLD_RATIO,
        f"profitable in {good} of {folds} blocks", {"profitable": good, "folds": folds}))

    if mc and "error" not in mc:
        down = float(mc.get("prob_loss_pct", 100.0))
        ruin = float(mc.get("prob_ruin_pct", 100.0))
        checks.append(Check(
            "survives a re-deal", down <= MAX_PROB_LOSS_PCT,
            f"{down:g}% of re-deals ended down (limit {MAX_PROB_LOSS_PCT:g}%)", down))
        checks.append(Check(
            "no real risk of ruin", ruin <= MAX_PROB_RUIN_PCT,
            f"{ruin:g}% of re-deals lost half the account (limit {MAX_PROB_RUIN_PCT:g}%)", ruin))
    else:
        checks.append(Check("survives a re-deal", False,
                            mc.get("error", "not enough out-of-sample trades to re-deal") if mc
                            else "no out-of-sample trades to re-deal"))

    sweeps = sens.get("sweeps") or []
    spikes = [s["param"] for s in sweeps if s.get("shape") == "spike"]
    ratio_spikes = len(spikes) / len(sweeps) if sweeps else 1.0
    checks.append(Check(
        "settings sit on plateaus", bool(sweeps) and ratio_spikes <= MAX_SPIKE_RATIO,
        (f"{len(spikes)} of {len(sweeps)} settings are lone peaks"
         + (f" ({', '.join(spikes)})" if spikes else "")) if sweeps else "no sweep was possible",
        spikes))
    return checks


def _verdict(v: Validation) -> str:
    failed = [c for c in v.checks if not c.passed]
    if not failed:
        return ("Every check passed. That is permission to paper-trade it and then risk a small amount, "
                "not a promise: out-of-sample history is still history. Run this again on newer data "
                "before increasing size, and keep the daily loss limit where it is.")
    worst = "; ".join(f"{c.name} ({c.detail})" for c in failed[:3])
    tail = f" and {len(failed) - 3} more" if len(failed) > 3 else ""
    return (f"Do not trade this with real money. It failed {len(failed)} of {len(v.checks)} checks: "
            f"{worst}{tail}. A backtest that fails here has found something true about this "
            f"particular history and nothing about the next one.")


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from bot.config import load_config
    from bot.common import parse_date_ms
    from bot.data.history import cache_path, download_ohlcv, load_csv
    from bot.logging_utils import setup_logging

    p = argparse.ArgumentParser(prog="python -m bot.validate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", help="candles to validate against (default: download them)")
    p.add_argument("--symbol", default=None, help="market to download when no CSV is given")
    p.add_argument("--timeframe", default=None)
    p.add_argument("--from", dest="since", default=None, help="YYYY-MM-DD")
    p.add_argument("--to", dest="until", default=None, help="YYYY-MM-DD")
    p.add_argument("--config", default=None)
    p.add_argument("--strategy", default=None)
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--sample", type=int, default=60, help="parameter sets per block (0 = the whole grid)")
    p.add_argument("--runs", type=int, default=2000, help="Monte Carlo re-deals")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--out", default=None, help="also write the full result as JSON here")
    p.add_argument("--json", action="store_true", help="print JSON instead of the report")
    args = p.parse_args(argv)

    setup_logging("WARNING", "text")
    cfg = load_config(args.config)
    if args.symbol or args.timeframe:
        cfg = cfg.model_copy(update={"exchange": cfg.exchange.model_copy(update={
            k: v for k, v in (("symbol", args.symbol), ("timeframe", args.timeframe)) if v})})

    if args.csv:
        df = load_csv(args.csv)
    else:
        from bot.execution.live import LiveExchange

        since = parse_date_ms(args.since) if args.since else None
        until = parse_date_ms(args.until) + 86_400_000 - 1 if args.until else None
        if since is None or until is None:
            p.error("downloading needs --from and --to (or pass --csv)")
        market = LiveExchange(cfg.exchange, None)
        cache = cache_path("data/history", cfg.exchange.id, cfg.exchange.symbol, cfg.exchange.timeframe)
        print(f"downloading {cfg.exchange.symbol} {cfg.exchange.timeframe}...", file=sys.stderr)
        df = download_ohlcv(market, symbol=cfg.exchange.symbol, timeframe=cfg.exchange.timeframe,
                            since_ms=since, until_ms=until, cache=cache)

    if args.since and args.csv:
        df = df[df["ts"] >= parse_date_ms(args.since)]
    if args.until and args.csv:
        df = df[df["ts"] <= parse_date_ms(args.until) + 86_400_000 - 1]
    df = df.reset_index(drop=True)

    result = validate(df, cfg, strategy_name=args.strategy, folds=args.folds,
                      sample=args.sample or None, runs=args.runs, workers=args.workers,
                      progress=lambda text: print(f"  {text}...", file=sys.stderr, flush=True))
    if args.out:
        Path(args.out).write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
        print(f"written to {args.out}", file=sys.stderr)
    print(json.dumps(result.to_dict(), indent=2, default=str) if args.json else result.report())
    return 0 if result.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
