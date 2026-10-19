"""Effective settings = ``config.yaml`` + overrides saved from the Settings page.

Overrides are validated with exactly the same models as the file (so the hard limits apply
to them too) before anything is written. A rejected change leaves the stored settings
untouched. Every accepted change is logged with who made it.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from app.config import EDITABLE_SECTIONS, Settings, apply_overrides
from app.core.clock import now_ms
from app.core.errors import InvalidRequest
from app.monitoring.logging import get_logger, log_event
from app.storage.database import Database, from_json, to_json

log = get_logger("settings")

Listener = Callable[[Settings], None]


class SettingsStore:
    def __init__(self, base: Settings, db: Database) -> None:
        self.base = base
        self.db = db
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._current = self._build()

    def _overrides(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in self.db.query("SELECT section, key, value FROM settings_overrides ORDER BY section, key"):
            out.setdefault(row["section"], {})[row["key"]] = from_json(row["value"])
        return out

    def _build(self) -> Settings:
        overrides = self._overrides()
        try:
            return apply_overrides(self.base, overrides)
        except ValueError as exc:
            # A config file edited after the overrides were saved can make an old override
            # invalid. Fall back to the file rather than refusing to start, and say so.
            log_event(log, 30, "stored settings overrides are no longer valid and were ignored",
                      error=str(exc))
            return self.base

    @property
    def current(self) -> Settings:
        with self._lock:
            return self._current

    def overrides(self) -> dict[str, dict[str, Any]]:
        return self._overrides()

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def update(self, changes: dict[str, dict[str, Any]], *, actor: str) -> Settings:
        if actor not in {"user", "cli", "test"}:
            # The research assistant and every automated component are deliberately excluded.
            raise InvalidRequest(f"settings cannot be changed by {actor!r}")
        with self._lock:
            merged = self._overrides()
            for section, values in changes.items():
                if section not in EDITABLE_SECTIONS:
                    raise InvalidRequest(f"section {section!r} cannot be changed at runtime")
                if not isinstance(values, dict):
                    raise InvalidRequest(f"section {section!r} must be an object")
                merged.setdefault(section, {}).update(values)
            try:
                candidate = apply_overrides(self.base, merged)
            except ValueError as exc:
                raise InvalidRequest("the new settings are not valid", details={"errors": _errors(exc)}) from None
            ts = now_ms()
            with self.db.transaction():
                for section, values in changes.items():
                    for key, value in values.items():
                        self.db.execute(
                            "INSERT INTO settings_overrides(section, key, value, updated_at, updated_by) "
                            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(section, key) DO UPDATE SET "
                            "value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                            (section, key, to_json(value), ts, actor))
            self._current = candidate
        log_event(log, 20, "settings changed", actor=actor, changes=changes)
        self._notify(candidate)
        return candidate

    def reset(self, section: str | None = None, *, actor: str) -> Settings:
        with self._lock:
            if section is None:
                self.db.execute("DELETE FROM settings_overrides")
            else:
                self.db.execute("DELETE FROM settings_overrides WHERE section = ?", (section,))
            self._current = self._build()
            current = self._current
        log_event(log, 20, "settings reset to the configuration file", actor=actor, section=section or "all")
        self._notify(current)
        return current

    def _notify(self, settings: Settings) -> None:
        for listener in list(self._listeners):
            try:
                listener(settings)
            except Exception:  # a listener must not stop the others
                log.exception("settings listener failed")


def _errors(exc: ValueError) -> list[dict[str, Any]]:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return [{"loc": ".".join(str(p) for p in e.get("loc", ())), "msg": e.get("msg", "")}
                    for e in errors()]
        except Exception:  # pragma: no cover
            pass
    return [{"loc": "", "msg": str(exc)}]
