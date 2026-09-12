"""Desktop entry point: python -m bot.ui  (or the BTCBot executable).

Starts the local API server, then opens the dashboard in a native window (pywebview)
or in the default browser. Closing the window or pressing Quit stops the bot cleanly."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from bot.config import load_dotenv
from bot.logging_utils import log_event, setup_logging
from bot.ui.app import VERSION, BotController, app_dir, is_frozen, prepare_app_dir
from bot.ui.server import UIServer

log = logging.getLogger("bot.ui")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="BTCBot", description=__doc__)
    p.add_argument("--root", help="application directory (default: next to the exe, or the repo root)")
    p.add_argument("--port", type=int, default=0, help="fixed port for the UI (default: random free port)")
    p.add_argument("--browser", action="store_true", help="always open the system browser instead of a native window")
    p.add_argument("--no-open", action="store_true", help="start the server only; print the URL")
    p.add_argument("--smoke", action="store_true", help="start, call the API once, exit (used by the build check)")
    p.add_argument("--replay", metavar="CSV", help="start a paper replay of this CSV immediately")
    return p.parse_args(argv)


def _open_native(url: str, quit_event: threading.Event) -> bool:
    """Try a native window through pywebview. Returns False if that is not possible."""
    try:
        import webview  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log.info("pywebview not available (%s); using the browser", type(exc).__name__)
        return False
    try:
        window = webview.create_window("BTC Bot", url, width=1280, height=840, min_size=(960, 640))

        def watch() -> None:
            quit_event.wait()
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=watch, daemon=True).start()
        webview.start()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("native window failed (%s: %s); using the browser", type(exc).__name__, exc)
        return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve() if args.root else app_dir()
    prepare_app_dir(root)
    os.chdir(root)
    load_dotenv(root / ".env")
    controller = BotController(root)
    setup_logging("INFO", "text", stdout=not is_frozen() and not args.smoke, file_path=str(root / "data" / "bot.log"),
                  extra_handlers=[controller.events])
    server = UIServer(controller, port=args.port)
    server.start()
    log_event(log, "ui_started", version=VERSION, url=server.url, root=str(root), frozen=is_frozen())

    if args.smoke:
        req = urllib.request.Request(server.url + "api/status", headers={"X-Token": server.token})
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = json.loads(resp.read().decode("utf-8"))
        # Prove the frozen bundle carries the exchange client and the strategy stack (no network).
        import ccxt  # noqa: F401

        from bot.strategy import get_strategy

        cfg = controller.load_cfg()
        exchange_cls = getattr(ccxt, cfg.exchange.id)
        exchange_cls({"enableRateLimit": True})
        strategy = get_strategy(cfg.strategy.name, cfg.strategy.params)
        print(json.dumps({"ok": True, "version": status["version"], "state": status["state"],
                          "config": status["config"], "root": str(root), "ccxt": ccxt.__version__,
                          "strategy_warmup": strategy.warmup, "samples": len(controller.samples())}))
        server.shutdown()
        return 0

    if args.replay:
        controller.start(replay=args.replay)

    print(f"BTC Bot UI: {server.url}", flush=True)
    try:
        opened = False
        if not args.no_open and not args.browser:
            opened = _open_native(server.url, controller.quit_event)
        if not opened:
            if not args.no_open:
                webbrowser.open(server.url)
            while not controller.quit_event.is_set():
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop(wait=20.0)
        server.shutdown()
        log_event(log, "ui_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
