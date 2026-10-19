"""The application's components, created once and wired together here.

Nothing in the domain packages reaches for a global: each component receives what it needs.
The API reads this container from ``app.state.services``; tests build their own with a
temporary directory and a fake exchange.
"""
from __future__ import annotations

import os
from typing import Any

from app.config import Settings
from app.core.jobs import JobManager
from app.monitoring.health import HealthMonitor
from app.monitoring.logging import configure_logging, get_logger, shutdown_logging
from app.storage.database import Database
from app.storage.schema import MARKET_MIGRATIONS, STATE_MIGRATIONS
from app.storage.settings_store import SettingsStore

log = get_logger("services")


class Services:
    def __init__(self, settings: Settings, *, start_background: bool = True, console_logs: bool = True) -> None:
        self.start_background = start_background
        self.db = Database(settings.database_path, STATE_MIGRATIONS, name="trader")
        self.market_db = Database(settings.market_database_path, MARKET_MIGRATIONS, name="market")
        self.log_buffer = configure_logging(self.db, console=console_logs)
        self.settings_store = SettingsStore(settings, self.db)
        self.jobs = JobManager()
        self.health = HealthMonitor(self.db)
        self.health.register("database", self._database_probe)

    # ----- settings ----------------------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self.settings_store.current

    @staticmethod
    def anthropic_key_present() -> bool:
        """Only ever reports presence. The value itself never leaves this process."""
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    # ----- lifecycle -----------------------------------------------------------------------
    async def start(self) -> None:
        log.info("services started (background tasks %s)", "on" if self.start_background else "off")

    async def stop(self) -> None:
        self.jobs.shutdown()
        log.info("services stopped")
        shutdown_logging()
        self.db.close()
        self.market_db.close()

    # ----- probes ------------------------------------------------------------------------
    def _database_probe(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "state_db": {"path": self.db.path, "schema": self.db.schema_version, "bytes": self.db.size_bytes()},
            "market_db": {"path": self.market_db.path, "schema": self.market_db.schema_version,
                          "bytes": self.market_db.size_bytes()},
        }
