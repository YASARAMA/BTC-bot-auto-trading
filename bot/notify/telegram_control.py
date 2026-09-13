"""Two-way Telegram control and a watchdog.

Polls Telegram for commands and answers them. Only the chat id in the configuration is
obeyed: a message from anyone else is ignored and logged, because these commands can stop
trading and, with a license, move real money.

Commands: /status /pnl /position /stop /start /kill /unkill /why /help
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from bot.common import now_ms

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"

HELP = """BTC Bot commands:
/status - mode, equity, position, risk
/pnl - today and overall profit
/position - the open position and its levels
/why - what the bot is waiting for
/stop - stop trading (the app stays open)
/start - start trading in the configured mode
/kill - kill switch ON: block every order
/unkill - kill switch off
/help - this list"""


class TelegramControl:
    """Long-polls Telegram in a background thread and runs the commands it receives."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        handlers: dict[str, Callable[[], str]],
        *,
        poll_seconds: float = 2.0,
        request: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.handlers = handlers
        self.poll_seconds = poll_seconds
        self._request = request or self._http
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.offset = 0
        self.seen = 0
        self.rejected = 0
        self.last_error: str | None = None

    # ----- transport ------------------------------------------------------------------
    def _http(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        url = API.format(token=self.token, method=method) + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=max(10.0, float(params.get("timeout", 0)) + 10)) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def send(self, text: str) -> None:
        try:
            self._request("sendMessage", {"chat_id": self.chat_id, "text": text[:3900],
                                          "disable_web_page_preview": True})
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram send failed: %s", type(exc).__name__)

    # ----- commands -------------------------------------------------------------------
    def handle(self, text: str) -> str:
        command = (text or "").strip().split()[0].lower().lstrip("/").split("@")[0]
        if command in ("help", "start_help", ""):
            return HELP
        handler = self.handlers.get(command)
        if handler is None:
            return f"Unknown command {command!r}.\n\n{HELP}"
        try:
            return handler()
        except Exception as exc:  # noqa: BLE001 - never kill the poller over one command
            log.exception("telegram command %s failed", command)
            return f"{command} failed: {type(exc).__name__}: {exc}"

    def poll_once(self, timeout: int = 20) -> int:
        """One long-poll round. Returns how many messages were acted on."""
        data = self._request("getUpdates", {"offset": self.offset, "timeout": timeout})
        handled = 0
        for update in data.get("result", []):
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
            message = update.get("message") or update.get("edited_message") or {}
            chat = str((message.get("chat") or {}).get("id", ""))
            text = message.get("text") or ""
            if not text:
                continue
            self.seen += 1
            if chat != self.chat_id:
                self.rejected += 1
                log.warning("ignoring a Telegram command from chat %s (not the configured chat)", chat)
                continue
            reply = self.handle(text)
            self.send(reply)
            handled += 1
        return handled

    # ----- lifecycle ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            backoff = self.poll_seconds
            while not self._stop.is_set():
                try:
                    self.poll_once()
                    self.last_error, backoff = None, self.poll_seconds
                except Exception as exc:  # noqa: BLE001 - network hiccups are expected
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    backoff = min(60.0, backoff * 2)
                self._stop.wait(backoff)

        self._thread = threading.Thread(target=loop, daemon=True, name="telegram-control")
        self._thread.start()
        log.info("telegram control listening for commands")

    def stop(self) -> None:
        self._stop.set()


class Watchdog:
    """Alerts when the bot stops producing cycles, and when it recovers.

    A bot that dies quietly is worse than one that stops loudly: the position stays open
    with nothing watching it.
    """

    def __init__(
        self,
        *,
        is_running: Callable[[], bool],
        last_cycle_ms: Callable[[], int | None],
        notify: Callable[[str], None],
        stall_seconds: float = 900.0,
        check_seconds: float = 60.0,
    ) -> None:
        self.is_running = is_running
        self.last_cycle_ms = last_cycle_ms
        self.notify = notify
        self.stall_seconds = stall_seconds
        self.check_seconds = check_seconds
        self.alerted = False
        self.alerts = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self, now: int | None = None) -> str | None:
        now = now if now is not None else now_ms()
        if not self.is_running():
            self.alerted = False
            return None
        last = self.last_cycle_ms()
        if last is None:
            return None
        silent = (now - last) / 1000.0
        if silent > self.stall_seconds and not self.alerted:
            self.alerted = True
            self.alerts += 1
            text = (f"No trading cycle for {silent / 60:.0f} minutes. The bot may be stuck or the "
                    f"exchange unreachable. An open position is not being watched.")
            self.notify(text)
            return text
        if silent <= self.stall_seconds and self.alerted:
            self.alerted = False
            self.notify("Trading cycles resumed; the bot is alive again.")
        return None

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(self.check_seconds):
                try:
                    self.check()
                except Exception:  # noqa: BLE001
                    log.exception("watchdog check failed")

        self._thread = threading.Thread(target=loop, daemon=True, name="watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
