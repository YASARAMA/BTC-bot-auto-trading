"""Telegram / Discord notifications. Fully optional: with no credentials every call
is a no-op, and a failing webhook never raises into the trading loop."""
from __future__ import annotations

import logging
from typing import Any, Callable

from bot.config import NotifyConfig, Secrets

log = logging.getLogger(__name__)


class Notifier:
    def __init__(
        self,
        cfg: NotifyConfig,
        secrets: Secrets | None = None,
        *,
        post: Callable[..., Any] | None = None,
        prefix: str = "",
    ) -> None:
        self.cfg = cfg
        s = secrets or Secrets()
        self.telegram = (s.telegram_bot_token, s.telegram_chat_id) if s.telegram_bot_token and s.telegram_chat_id else None
        self.discord = s.discord_webhook_url
        self.prefix = prefix
        self._post = post
        self.sent: list[str] = []  # last messages, handy for tests and /status

    @property
    def enabled(self) -> bool:
        return bool(self.telegram or self.discord)

    def _http_post(self, url: str, **kwargs: Any) -> None:
        if self._post is not None:
            self._post(url, **kwargs)
            return
        import requests

        resp = requests.post(url, timeout=self.cfg.timeout_seconds, **kwargs)
        resp.raise_for_status()

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        msg = f"{self.prefix}{text}" if self.prefix else text
        self.sent = (self.sent + [msg])[-50:]
        if self.telegram:
            token, chat_id = self.telegram
            try:
                self._http_post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": msg, "disable_web_page_preview": True},
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram notification failed: %s", type(exc).__name__)
        if self.discord:
            try:
                self._http_post(self.discord, json={"content": msg[:1900]})
            except Exception as exc:  # noqa: BLE001
                log.warning("discord notification failed: %s", type(exc).__name__)

    # Convenience wrappers gated by config.
    def fill(self, text: str) -> None:
        if self.cfg.on_fill:
            self.send(f"FILL {text}")

    def halt(self, text: str) -> None:
        if self.cfg.on_halt:
            self.send(f"HALT {text}")

    def error(self, text: str) -> None:
        if self.cfg.on_error:
            self.send(f"ERROR {text}")
