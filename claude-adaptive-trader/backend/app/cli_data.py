"""Data commands: download history from Binance, import CSV files, list what is stored."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.config import load_settings
from app.core.clock import iso


def _services(args: argparse.Namespace):
    from app.services import Services

    return Services(load_settings(args.config), start_background=False)


def _close(svc) -> None:
    asyncio.run(svc.stop())


def _download(args: argparse.Namespace) -> int:
    svc = _services(args)
    try:
        s = svc.settings
        pairs = args.pairs or s.exchange.pairs
        tfs = args.timeframes or [s.exchange.signal_timeframe, *s.exchange.confirm_timeframes]
        print(f"Downloading {', '.join(pairs)} | {', '.join(tfs)} | {args.days or s.exchange.history_days} days")
        failures = 0
        for res in svc.market.sync_many(pairs, tfs, days=args.days):
            if "error" in res:
                failures += 1
                print(f"  {res['pair']:<10} {res['timeframe']:<4} FAILED: {res['error']}")
            else:
                print(f"  {res['pair']:<10} {res['timeframe']:<4} +{res['written']} candles, "
                      f"{iso(res['first_ts'])} .. {iso(res['last_ts'])}, gaps: {res['gaps']}")
        return 1 if failures else 0
    finally:
        _close(svc)


def _import_csv(args: argparse.Namespace) -> int:
    from app.data.importer import import_csv

    svc = _services(args)
    try:
        path = Path(args.file)
        res = import_csv(svc.candle_store, path, filename=path.name, exchange=args.exchange,
                         pair=args.pair.upper(), timeframe=args.timeframe)
        rep = res["report"]
        print(f"Imported {res['written']} candles as {res['exchange']} {res['pair']} {res['timeframe']} "
              f"({res['source']}); {rep['missing_bars']} missing, {rep['dropped_invalid']} invalid rows removed")
        for issue in rep["issues"]:
            print(f"  - {issue}")
        return 0
    finally:
        _close(svc)


def _import_samples(args: argparse.Namespace) -> int:
    from app.data.importer import import_repo_samples

    svc = _services(args)
    try:
        for res in import_repo_samples(svc.candle_store):
            if "error" in res:
                print(f"  {res['file']}: {res['error']}")
            else:
                print(f"  {res['source']}: {res['written']} candles as {res['exchange']} {res['pair']} "
                      f"{res['timeframe']}")
        return 0
    finally:
        _close(svc)


def _inventory(args: argparse.Namespace) -> int:
    svc = _services(args)
    try:
        rows = svc.candle_store.inventory()
        if not rows:
            print("No candles stored yet. Run 'python -m app download' or 'python -m app import-samples'.")
        for r in rows:
            print(f"{r['exchange']:<9} {r['pair']:<10} {r['timeframe']:<4} {r['candles']:>8} candles "
                  f"{iso(r['first_ts'])} .. {iso(r['last_ts'])}  coverage {r['coverage_pct']}%  "
                  f"sources: {', '.join(f'{k} ({v})' for k, v in r['sources'].items())}")
        return 0
    finally:
        _close(svc)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("download", help="download or update historical candles from Binance (public data)")
    p.add_argument("--pairs", nargs="*", default=None)
    p.add_argument("--timeframes", nargs="*", default=None)
    p.add_argument("--days", type=int, default=None)
    p.set_defaults(func=_download)

    p = sub.add_parser("import-csv", help="import candles from a CSV file")
    p.add_argument("file")
    p.add_argument("--exchange", required=True, help="the exchange the file really comes from")
    p.add_argument("--pair", required=True)
    p.add_argument("--timeframe", required=True)
    p.set_defaults(func=_import_csv)

    p = sub.add_parser("import-samples", help="import the real sample candle files shipped in data/samples")
    p.set_defaults(func=_import_samples)

    p = sub.add_parser("inventory", help="list the stored candle series")
    p.set_defaults(func=_inventory)
