"""Health of every component, in one place.

Components register a probe that returns a small dictionary with at least a ``status`` of
``ok``, ``degraded``, ``error`` or ``disabled``. The overall status is the worst one, with
``disabled`` counting as fine: an optional feature that is switched off is not a fault.

A *critical* error is different from a failing probe. When the trading engine hits one it
calls ``raise_critical``: new entries are blocked (risk check 14) until a person has read the
diagnostic record and cleared it.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from app.core.clock import now_ms
from app.monitoring.logging import get_logger, log_event
from app.storage.database import Database

log = get_logger("health")

Probe = Callable[[], dict[str, Any]]
_RANK = {"ok": 0, "disabled": 0, "degraded": 1, "error": 2}

CRITICAL_KEY = "critical_error"


class HealthMonitor:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.started_at = now_ms()
        self._probes: dict[str, Probe] = {}
        self._lock = threading.Lock()

    def register(self, name: str, probe: Probe) -> None:
        with self._lock:
            self._probes[name] = probe

    # ----- critical errors ------------------------------------------------------------
    def raise_critical(self, source: str, message: str, **context: Any) -> None:
        record = {"source": source, "message": message, "ts": now_ms(), "context": context}
        self.db.set_state(CRITICAL_KEY, record)
        log_event(log, 50, f"critical error in {source}: {message}; new entries are blocked", **context)

    def critical(self) -> dict[str, Any] | None:
        value = self.db.get_state(CRITICAL_KEY)
        return value if isinstance(value, dict) else None

    def clear_critical(self, actor: str, note: str = "") -> None:
        previous = self.critical()
        self.db.delete_state(CRITICAL_KEY)
        log_event(log, 30, "critical error cleared", actor=actor, note=note, previous=previous)

    # ----- report ----------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            probes = dict(self._probes)
        components: dict[str, Any] = {}
        for name, probe in probes.items():
            started = time.perf_counter()
            try:
                result = dict(probe())
            except Exception as exc:  # a broken probe is itself an error worth seeing
                result = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
            result.setdefault("status", "ok")
            result["probe_ms"] = round((time.perf_counter() - started) * 1000, 2)
            components[name] = result
        critical = self.critical()
        worst = max((_RANK.get(c["status"], 2) for c in components.values()), default=0)
        if critical:
            worst = 2
        status = {0: "ok", 1: "degraded", 2: "error"}[worst]
        return {
            "status": status,
            "ts": now_ms(),
            "uptime_s": round((now_ms() - self.started_at) / 1000, 1),
            "critical_error": critical,
            "components": components,
        }
