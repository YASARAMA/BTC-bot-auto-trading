"""Tiny JSON API + static file server on 127.0.0.1, standard library only.

Every /api call must carry the per-run token (X-Token header) that index.html embeds,
so a web page from another origin cannot drive the bot through the browser."""
from __future__ import annotations

import json
import logging
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from bot.config import LiveSafetyError
from bot.ui.app import BotController

log = logging.getLogger("bot.ui.server")
STATIC_DIR = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def build_routes(c: BotController) -> dict[tuple[str, str], Callable[[dict[str, Any], dict[str, Any]], Any]]:
    def start(q: dict[str, Any], body: dict[str, Any]) -> Any:
        try:
            return c.start(replay=body.get("replay") or None, replay_delay=float(body.get("replay_delay", 0.25)),
                           confirm_live=bool(body.get("confirm_live")))
        except LiveSafetyError as exc:
            raise ApiError(403, str(exc)) from exc
        except FileNotFoundError as exc:
            raise ApiError(404, str(exc)) from exc
        except RuntimeError as exc:
            raise ApiError(409, str(exc)) from exc

    def save_config(q: dict[str, Any], body: dict[str, Any]) -> Any:
        try:
            if "yaml" in body:
                c.save_config_yaml(str(body["yaml"]))
            else:
                c.save_config(body.get("config") or body)
        except ValidationError as exc:
            raise ApiError(400, "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())) from exc
        except Exception as exc:  # noqa: BLE001
            raise ApiError(400, f"{type(exc).__name__}: {exc}") from exc
        return {"ok": True, "config": c.config_dict()}

    def start_backtest(q: dict[str, Any], body: dict[str, Any]) -> Any:
        try:
            return c.start_backtest(body)
        except RuntimeError as exc:
            raise ApiError(409, str(exc)) from exc

    def quit_app(q: dict[str, Any], body: dict[str, Any]) -> Any:
        threading.Thread(target=c.quit, daemon=True).start()
        return {"ok": True}

    return {
        ("GET", "/api/status"): lambda q, b: c.status(),
        ("GET", "/api/events"): lambda q, b: {"events": c.events.since(int(q.get("since", 0)), int(q.get("limit", 500)))},
        ("GET", "/api/trades"): lambda q, b: {"trades": c.trades(int(q.get("limit", 500)))},
        ("GET", "/api/equity"): lambda q, b: {"equity": c.equity(int(q.get("limit", 2000)))},
        ("GET", "/api/config"): lambda q, b: {"config": c.config_dict(), "yaml": c.config_yaml()},
        ("POST", "/api/config"): save_config,
        ("GET", "/api/secrets"): lambda q, b: c.secrets_status(),
        ("POST", "/api/secrets"): lambda q, b: c.save_secrets(b),
        ("GET", "/api/samples"): lambda q, b: {"samples": c.samples()},
        ("POST", "/api/start"): start,
        ("POST", "/api/stop"): lambda q, b: c.stop(wait=float(b.get("wait", 0.0))),
        ("POST", "/api/kill"): lambda q, b: {"kill_switch": c.kill_switch(b.get("active"))},
        ("GET", "/api/backtest"): lambda q, b: c.backtest_status(),
        ("POST", "/api/backtest"): start_backtest,
        ("POST", "/api/quit"): quit_app,
    }


def make_handler(controller: BotController, token: str) -> type[BaseHTTPRequestHandler]:
    routes = build_routes(controller)

    class Handler(BaseHTTPRequestHandler):
        server_version = "BTCBotUI/1"

        def log_message(self, fmt: str, *args: Any) -> None:  # keep stderr clean (it may not exist)
            log.debug("http %s", fmt % args)

        # ----- helpers -----
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            file = (STATIC_DIR / name).resolve()
            if not str(file).startswith(str(STATIC_DIR.resolve())) or not file.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            data = file.read_bytes()
            if name == "index.html":
                data = data.replace(b"__TOKEN__", token.encode("ascii"))
            self._send(HTTPStatus.OK, data, CONTENT_TYPES.get(file.suffix, "application/octet-stream"))

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                if method == "GET":
                    self._static(url.path)
                else:
                    self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"", "text/plain")
                return
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing or invalid token"})
                return
            handler = routes.get((method, url.path))
            if handler is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": f"no route {method} {url.path}"})
                return
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            body: dict[str, Any] = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if raw:
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be JSON"})
                        return
                    if not isinstance(body, dict):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be a JSON object"})
                        return
            try:
                self._json(HTTPStatus.OK, handler(query, body))
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                log.exception("api %s %s failed", method, url.path)
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_OPTIONS(self) -> None:  # noqa: N802 - no CORS: cross-origin preflights fail on purpose
            self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")

    return Handler


class UIServer:
    def __init__(self, controller: BotController, host: str = "127.0.0.1", port: int = 0, token: str | None = None) -> None:
        self.token = token or secrets.token_urlsafe(24)
        self.httpd = ThreadingHTTPServer((host, port), make_handler(controller, self.token))
        self.httpd.daemon_threads = True
        self.host, self.port = self.httpd.server_address[0], self.httpd.server_address[1]
        self.url = f"http://{self.host}:{self.port}/"
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.25},
                                        daemon=True, name="ui-http")
        self._thread.start()

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
