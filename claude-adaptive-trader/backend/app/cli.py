"""Command line: ``python -m app <command>`` from the ``backend`` directory.

Commands that change a running server's state (paper trading) talk to it over HTTP so that
there is always exactly one process owning the database.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from app.config import load_settings


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from app.main import create_app

    settings = load_settings(args.config)
    host = args.host or settings.server.host
    port = args.port or settings.server.port
    if host not in {"127.0.0.1", "localhost", "::1"} and not args.allow_remote:
        print("Refusing to listen on a non-loopback address without --allow-remote.", file=sys.stderr)
        return 2
    print(f"Claude Adaptive Trader on http://{host}:{port}  (paper trading only; Ctrl+C to stop)")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app", description="Claude Adaptive Trader")
    parser.add_argument("--config", default=None, help="path to config.yaml (default: project root)")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the API and the web interface")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--allow-remote", action="store_true", help="allow a non-loopback address (not advised)")
    serve.set_defaults(func=_serve)

    for register in _extra_commands():
        register(sub)
    return parser


def _extra_commands() -> list[Any]:
    import importlib
    import importlib.util

    out: list[Any] = []
    for name in ("cli_data", "cli_research"):
        if importlib.util.find_spec(f"app.{name}") is not None:
            out.append(importlib.import_module(f"app.{name}").register)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)
