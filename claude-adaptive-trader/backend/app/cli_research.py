"""Research commands: backtest, validate, selector-validate, final-test, and paper trading control of a
running server."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.config import load_settings


def _runner(args: argparse.Namespace):
    from app.backtesting.runner import ResearchRunner
    from app.research.experiment_tracker import ExperimentTracker
    from app.services import Services

    svc = Services(load_settings(args.config), start_background=False)
    return svc, ResearchRunner(lambda: svc.settings, svc.db, svc.market, ExperimentTracker(svc.db))


def _progress(fraction: float, message: str) -> None:
    print(f"\r  [{fraction * 100:5.1f}%] {message[:90]:<90}", end="", file=sys.stderr, flush=True)


class _Ctx:
    def progress(self, fraction: float, message: str = "") -> None:
        _progress(fraction, message)

    def check(self) -> None:
        pass


def _print(result: dict) -> None:
    print("", file=sys.stderr)
    keys = ("experiment_id", "verdict", "verdict_message", "trades", "net_return_pct", "expectancy_r", "profit_factor",
            "max_drawdown_pct", "win_rate_pct", "benchmark_return_pct", "failed_gates", "version_id")
    for k in keys:
        if k in result:
            print(f"{k:>22}: {result[k]}")
    if "stress" in result:
        for name, m in result["stress"].items():
            print(f"{'cost ' + name:>22}: {m}")


def _backtest(args: argparse.Namespace) -> int:
    from app.backtesting.runner import BacktestRequest

    svc, runner = _runner(args)
    try:
        req = BacktestRequest(strategy=args.strategy, params=json.loads(args.params or "{}"), pairs=args.pairs,
                              timeframe=args.timeframe, exchange=args.exchange, segment=args.segment,
                              random_baseline=args.random_baseline)
        _print(runner.backtest(req, _Ctx(), created_by="cli"))
        return 0
    finally:
        asyncio.run(svc.stop())


def _validate(args: argparse.Namespace) -> int:
    from app.backtesting.runner import ValidationRequest

    svc, runner = _runner(args)
    try:
        req = ValidationRequest(strategy=args.strategy, params=json.loads(args.params or "{}"), pairs=args.pairs,
                                timeframe=args.timeframe, exchange=args.exchange)
        _print(runner.validate(req, _Ctx(), created_by="cli"))
        return 0
    finally:
        asyncio.run(svc.stop())


def _selector_validate(args: argparse.Namespace) -> int:
    from app.backtesting.runner import SelectorRequest

    svc, runner = _runner(args)
    try:
        req = SelectorRequest(pairs=args.pairs, timeframe=args.timeframe, exchange=args.exchange)
        result = runner.selector_validation(req, _Ctx(), created_by="cli")
        _print(result)
        for name, m in (result.get("single_strategies") or {}).items():
            print(f"{'alone: ' + name:>22}: {m}")
        return 0
    finally:
        asyncio.run(svc.stop())


def _final_test(args: argparse.Namespace) -> int:
    from app.backtesting.runner import FinalTestRequest

    svc, runner = _runner(args)
    try:
        req = FinalTestRequest(version_id=args.version_id, reason=args.reason, pairs=args.pairs,
                               timeframe=args.timeframe, exchange=args.exchange)
        _print(runner.final_test(req, _Ctx(), actor="user"))
        return 0
    finally:
        asyncio.run(svc.stop())


def _paper(args: argparse.Namespace) -> int:
    """Talk to the running server, which owns the paper-trading state."""
    import httpx

    settings = load_settings(args.config)
    base = f"http://{settings.server.host}:{settings.server.port}/api/paper"
    headers = {"X-CAT-Client": "cli"}
    try:
        if args.action == "status":
            r = httpx.get(f"{base}/status", timeout=10)
        elif args.action == "start":
            body = {"data_mode": args.data_mode, "strategy_mode": args.strategy_mode, "strategy": args.strategy,
                    "pairs": args.pairs, "timeframe": args.timeframe, "exchange": args.exchange}
            r = httpx.post(f"{base}/start", json={k: v for k, v in body.items() if v is not None}, headers=headers,
                           timeout=30)
        elif args.action == "kill-switch-on":
            r = httpx.post(f"{base}/kill-switch", json={"on": True, "reason": "command line"}, headers=headers, timeout=10)
        elif args.action == "kill-switch-off":
            r = httpx.post(f"{base}/kill-switch", json={"on": False, "reason": "command line"}, headers=headers,
                           timeout=10)
        else:
            r = httpx.post(f"{base}/{args.action}", json={}, headers=headers, timeout=30)
    except httpx.HTTPError as exc:
        print(f"The server is not reachable at {base}: {exc}. Start it with: python -m app serve", file=sys.stderr)
        return 2
    data = r.json()
    if r.status_code >= 400:
        print(f"Error {r.status_code}: {data.get('message')}", file=sys.stderr)
        return 1
    print(json.dumps({k: data.get(k) for k in ("status", "label", "session", "portfolio", "kill_switch", "last_error")
                      if k in data} or data, indent=2, default=str))
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    def scope(p: argparse.ArgumentParser) -> None:
        p.add_argument("--pairs", nargs="*", default=None)
        p.add_argument("--timeframe", default=None)
        p.add_argument("--exchange", default="binance", help="where the stored candles come from")

    p = sub.add_parser("backtest", help="backtest one strategy on the development or validation segment")
    p.add_argument("--strategy", required=True)
    p.add_argument("--params", default=None, help='JSON object of parameters, e.g. \'{"target_r": 2.5}\'')
    p.add_argument("--segment", default="development", choices=["development", "validation", "research"])
    p.add_argument("--random-baseline", action="store_true")
    scope(p)
    p.set_defaults(func=_backtest)

    p = sub.add_parser("validate", help="run the predefined validation protocol and the acceptance gates")
    p.add_argument("--strategy", required=True)
    p.add_argument("--params", default=None)
    scope(p)
    p.set_defaults(func=_validate)

    p = sub.add_parser("selector-validate", help="walk-forward test of the adaptive selector itself")
    scope(p)
    p.set_defaults(func=_selector_validate)

    p = sub.add_parser("final-test", help="evaluate a validated version once on the frozen test set (logged)")
    p.add_argument("--version-id", type=int, required=True)
    p.add_argument("--reason", required=True)
    scope(p)
    p.set_defaults(func=_final_test)

    p = sub.add_parser("paper", help="control paper trading on a running server")
    p.add_argument("action", choices=["status", "start", "pause", "resume", "stop", "close-all", "kill-switch-on",
                                      "kill-switch-off"])
    p.add_argument("--data-mode", default="live", choices=["live", "replay"])
    p.add_argument("--strategy-mode", default="adaptive", choices=["adaptive", "forward_test"])
    p.add_argument("--strategy", default=None)
    scope(p)
    p.set_defaults(func=_paper)
