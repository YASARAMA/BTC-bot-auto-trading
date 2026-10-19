"""The application's components, created once and wired together here.

Nothing in the domain packages reaches for a global: each component receives what it needs.
The API reads this container from ``app.state.services``; tests build their own with a
temporary directory and a fake exchange.
"""
from __future__ import annotations

import os
from typing import Any

from app.config import Settings
from app.core.events import EventBus
from app.core.jobs import JobManager
from app.data.binance_client import BinancePublicClient
from app.data.historical_store import CandleStore
from app.data.live_feed import LiveFeed
from app.data.market_data import MarketDataService
from app.backtesting.runner import ResearchRunner
from app.execution.paper_trader import PaperTrader
from app.research.experiment_tracker import ExperimentTracker
from app.monitoring.health import HealthMonitor
from app.monitoring.logging import configure_logging, get_logger, shutdown_logging
from app.storage.database import Database
from app.storage.schema import MARKET_MIGRATIONS, STATE_MIGRATIONS
from app.storage.settings_store import SettingsStore

log = get_logger("services")


class Services:
    def __init__(self, settings: Settings, *, start_background: bool = True, console_logs: bool = True,
                 exchange: Any | None = None, ws_connect: Any | None = None) -> None:
        self.start_background = start_background
        self.db = Database(settings.database_path, STATE_MIGRATIONS, name="trader")
        self.market_db = Database(settings.market_database_path, MARKET_MIGRATIONS, name="market")
        self.log_buffer = configure_logging(self.db, console=console_logs)
        self.settings_store = SettingsStore(settings, self.db)
        self.jobs = JobManager()
        self.health = HealthMonitor(self.db)
        self.health.register("database", self._database_probe)

        # Market data: local cache, Binance public client, live feed.
        self.bus = EventBus()
        self.candle_store = CandleStore(self.market_db)
        ex = settings.exchange
        self.binance = BinancePublicClient(rate_limit_ms=ex.rate_limit_ms, timeout_ms=ex.request_timeout_ms,
                                           exchange=exchange)
        self.market = MarketDataService(lambda: self.settings, self.candle_store, self.binance, self.bus)
        self.live_feed = LiveFeed(lambda: self.settings, self.market, connect=ws_connect)
        self.health.register("market_data", self._market_probe)

        # Research.
        self.tracker = ExperimentTracker(self.db)
        self.research = ResearchRunner(lambda: self.settings, self.db, self.market, self.tracker)

        # Paper trading.
        self.paper = PaperTrader(lambda: self.settings, self.db, self.market, self.health, self.bus,
                                 strategy_params=self.strategy_params, selector_factory=None)
        self.health.register("paper_engine", self._paper_probe)

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
        import asyncio

        self.bus.bind(asyncio.get_running_loop())
        if self.start_background:
            self.live_feed.start()
            self.paper.restore()
        log.info("services started (background tasks %s)", "on" if self.start_background else "off")

    async def stop(self) -> None:
        self.paper.shutdown()
        await self.live_feed.stop()
        self.jobs.shutdown()
        log.info("services stopped")
        shutdown_logging()
        self.db.close()
        self.market_db.close()

    def strategy_params(self, name: str) -> dict[str, Any]:
        """Parameters for paper trading: the approved version if there is one, else the defaults."""
        row = self.db.query_one("SELECT params FROM strategy_versions WHERE strategy = ? AND status = 'approved'"
                                " ORDER BY approved_at DESC LIMIT 1", (name,))
        if row:
            from app.storage.database import from_json

            return from_json(row["params"], {}) or {}
        return {}

    # ----- probes ------------------------------------------------------------------------
    def _paper_probe(self) -> dict[str, Any]:
        st = self.paper.status()
        status = st["status"]
        health = "error" if status == "error" else ("ok" if status in {"running", "idle", "paused", "stopped"}
                                                    else "degraded")
        return {"status": health, "engine_status": status, "kill_switch": bool(st["kill_switch"].get("on")),
                "last_error": st.get("last_error")}

    def _market_probe(self) -> dict[str, Any]:
        st = self.market.status()
        return {"status": st["status"], "rest_reachable": st["rest"].get("reachable"),
                "rest_error": st["rest"].get("last_error"), "websocket_connected": st["websocket"].get("connected"),
                "websocket_error": st["websocket"].get("last_error"),
                "stale_series": [f"{x['pair']} {x['timeframe']}" for x in st["series"] if x["stale"]]}

    def _database_probe(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "state_db": {"path": self.db.path, "schema": self.db.schema_version, "bytes": self.db.size_bytes()},
            "market_db": {"path": self.market_db.path, "schema": self.market_db.schema_version,
                          "bytes": self.market_db.size_bytes()},
        }
