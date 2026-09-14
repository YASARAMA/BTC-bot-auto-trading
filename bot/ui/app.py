"""BotController: everything the UI can do, independent of HTTP.

Owns the bot thread, the in-memory event buffer, the kill switch, config and secret
files, read-only access to the SQLite state, and backtest jobs."""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from bot.backtest.__main__ import format_report
from bot.backtest.engine import run_backtest
from bot.common import iso, now_ms, parse_date_ms, timeframe_to_ms
from bot.config import (
    DOTENV_KEYS,
    BotConfig,
    LiveSafetyError,
    load_config,
    load_secrets,
    resolve_mode,
    write_dotenv,
)
from bot.data.feed import rows_to_frame
from bot.data.history import cache_path, download_ohlcv, load_csv
from bot.strategy import STRATEGIES, get_strategy
from bot.licensing import LicenseManager
from bot.logging_utils import EventBufferHandler, log_event
from bot.models import Side
from bot.modes import DEFAULT_MODE, MODES, apply_mode, detect_mode, mode_list
from bot.notify.notifier import Notifier as _Notifier  # noqa: F401  (used by _notify)
from bot.notify.telegram_control import TelegramControl, Watchdog
from bot.reports import analytics as trade_analytics, daily_summary, equity_csv, trades_csv
from bot.main import Runtime, build_runtime, run_loop
from bot.ui.updater import Updater, Version, read_build_info

log = logging.getLogger("bot.ui")

VERSION = "0.8.0"
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"]
EXCHANGES = ["binance", "bybit", "okx", "kraken", "coinbase", "kucoin", "bitget", "gateio", "mexc", "htx"]


def looks_temporary(path: Path) -> bool:
    """True when the app runs from a temp/extraction folder, where its files will not survive."""
    parts = [p.lower() for p in path.parts]
    return any(p in ("temp", "tmp") or p.startswith("rar$") or p.startswith("7z") for p in parts)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Where bundled read-only resources live (PyInstaller temp dir, or the repo)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def app_dir() -> Path:
    """Writable application directory: next to the exe, or the repo root in development."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def prepare_app_dir(root: Path) -> None:
    """First run: make sure config.yaml, data/ and the sample candles exist next to the app."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    src = bundle_dir()
    if not (root / "config.yaml").exists() and (src / "config.yaml").exists():
        shutil.copy(src / "config.yaml", root / "config.yaml")
    if (src / "CHANGELOG.md").exists():  # refreshed on every start so it matches the build
        shutil.copy(src / "CHANGELOG.md", root / "CHANGELOG.md")
    samples_src = src / "data" / "samples"
    if samples_src.is_dir():
        samples_dst = root / "data" / "samples"
        samples_dst.mkdir(parents=True, exist_ok=True)
        for f in samples_src.glob("*.csv"):
            if not (samples_dst / f.name).exists():
                shutil.copy(f, samples_dst / f.name)


def _downsample(points: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(i * step)] for i in range(limit)] + [points[-1]]


class BotController:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.config_path = self.root / "config.yaml"
        self.env_path = self.root / ".env"
        self.events = EventBufferHandler(maxlen=3000)
        self.events.listeners.append(self._on_event)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.quit_event = threading.Event()
        self.runtime: Runtime | None = None
        self.mode: str | None = None
        self.state = "stopped"  # stopped | starting | running | stopping | error
        self.error: str | None = None
        self.last_cycle: dict[str, Any] = {}
        self.cycles = 0
        self.started_at: int | None = None
        self.replay_path: str | None = None
        self.recent_alerts: list[dict[str, Any]] = []
        self.last_dropped_params: list[str] = []  # settings a save dropped as belonging elsewhere
        self._research_lock = threading.Lock()
        self._research_thread: threading.Thread | None = None
        self.research: dict[str, Any] = {"state": "idle"}
        self._download_lock = threading.Lock()
        self.download: dict[str, Any] = {"state": "idle"}
        self.telegram: TelegramControl | None = None
        self.watchdog: Watchdog | None = None
        self._last_report_day: str | None = None
        self._report_thread: threading.Thread | None = None
        self._bt_lock = threading.Lock()
        self._bt_thread: threading.Thread | None = None
        self.backtest: dict[str, Any] = {"state": "idle"}
        self.decisions: dict[str, int] = {}
        self.last_signals: list[dict[str, Any]] = []
        self._chart_cache: dict[str, Any] = {}
        self._chart_lock = threading.Lock()
        self.market_factory: Any = None  # tests inject a fake market data source here
        self.licenses = LicenseManager(self.root)
        self.build_info = read_build_info(bundle_dir(), VERSION)
        current = Version.parse(f"v{self.build_info['version']}-build.{self.build_info['build']}") or Version.parse(f"v{VERSION}")
        try:
            repo = self.load_cfg().update.repo
        except Exception:  # noqa: BLE001
            repo = "YASARAMA/BTC-bot-auto-trading"
        self.updater = Updater(
            repo=repo, current=current, app_dir=self.root,
            exe_path=Path(sys.executable) if is_frozen() else None, frozen=is_frozen(),
            token_getter=lambda: os.environ.get("GITHUB_TOKEN") or None, on_event=self._update_event,
        )
        self._notifier: Any = None

    # ----- config ------------------------------------------------------------------------
    def load_cfg(self) -> BotConfig:
        return load_config(self.config_path)

    def _abs(self, path: str) -> str:
        p = Path(path)
        return str(p if p.is_absolute() else (self.root / p).resolve())

    def runtime_cfg(self, cfg: BotConfig) -> BotConfig:
        """Config for the bot thread: relative file paths are anchored to the app folder,
        so the UI works no matter what the process working directory is."""
        return cfg.model_copy(update={
            "state": cfg.state.model_copy(update={"db_path": self._abs(cfg.state.db_path)}),
            "risk": cfg.risk.model_copy(update={"kill_switch_file": self._abs(cfg.risk.kill_switch_file or "data/KILL_SWITCH")}),
        })

    def config_dict(self) -> dict[str, Any]:
        return self.load_cfg().printable()

    def config_meta(self) -> dict[str, Any]:
        return {"strategies": sorted(STRATEGIES), "timeframes": TIMEFRAMES, "exchanges": EXCHANGES,
                "modes": mode_list()}

    def set_mode(self, mode: str) -> dict[str, Any]:
        """Apply a trading mode's risk limits and strategy parameters to config.yaml."""
        data = apply_mode(self.load_cfg().printable(), mode)
        cfg = self.save_config(data)
        log_event(log, "mode_changed", level=logging.WARNING, mode=mode,
                  detail=f"Trading mode set to {MODES[mode]['label']}: {MODES[mode]['summary']}",
                  risk=cfg.risk.model_dump())
        return {"mode": mode, "config": cfg.printable()}

    def config_yaml(self) -> str:
        return self.config_path.read_text(encoding="utf-8")

    def save_config(self, data: dict[str, Any]) -> BotConfig:
        data, self.last_dropped_params = self._prune_strategy_params(data)
        cfg = BotConfig.model_validate(data)
        text = yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        self.config_path.write_text("# Written by the BTC bot UI. See README.md for what each key means.\n" + text,
                                    encoding="utf-8")
        return cfg

    def save_config_yaml(self, text: str) -> BotConfig:
        raw = yaml.safe_load(text) or {}
        cfg = BotConfig.model_validate(raw)
        self.config_path.write_text(text, encoding="utf-8")
        return cfg

    def _prune_strategy_params(self, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Strip parameters left behind by a previous strategy before they reach disk.

        Switching from `ai` to `ema_rsi` used to leave `model` and `mode` in the file, and
        the next Start died on a validation error the user could do nothing about.
        """
        from bot.strategy import prune_params

        strategy = data.get("strategy")
        if not isinstance(strategy, dict) or not isinstance(strategy.get("params"), dict):
            return data, []
        clean, dropped = prune_params(str(strategy.get("name") or ""), strategy["params"])
        if not dropped:
            return data, []
        log.info("dropped %d strategy setting(s) that belong to another strategy: %s",
                 len(dropped), ", ".join(sorted(set(dropped))))
        return {**data, "strategy": {**strategy, "params": clean}}, sorted(set(dropped))

    # ----- secrets ------------------------------------------------------------------------
    def secrets_status(self) -> dict[str, Any]:
        return {
            "api_key_set": bool(os.environ.get("EXCHANGE_API_KEY")),
            "api_secret_set": bool(os.environ.get("EXCHANGE_API_SECRET")),
            "api_password_set": bool(os.environ.get("EXCHANGE_API_PASSWORD")),
            "telegram_set": bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
            "discord_set": bool(os.environ.get("DISCORD_WEBHOOK_URL")),
            "live_trading_env": (os.environ.get("LIVE_TRADING") or "").strip().lower() in {"1", "true", "yes", "on"},
            "env_file": str(self.env_path),
        }

    def save_secrets(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Empty string keeps the current value; None clears it; anything else replaces it."""
        clean: dict[str, str | None] = {}
        for key, value in updates.items():
            if key not in DOTENV_KEYS:
                continue
            if value is None:
                clean[key] = None
                os.environ.pop(key, None)
            elif isinstance(value, bool):
                clean[key] = "true" if value else "false"
                os.environ[key] = clean[key]
            elif isinstance(value, str) and value.strip():
                clean[key] = value.strip()
                os.environ[key] = value.strip()
        if clean:
            write_dotenv(self.env_path, clean)
        return self.secrets_status()

    # ----- licensing & changelog ------------------------------------------------------------
    def license_status(self) -> dict[str, Any]:
        return self.licenses.status()

    def activate_license(self, key: str) -> dict[str, Any]:
        lic = self.licenses.activate(key)
        if lic.valid:
            log_event(log, "license_activated", level=logging.WARNING, detail=f"License activated ({lic.masked})")
        return {**self.licenses.status(), "accepted": lic.valid, "reason": lic.reason}

    def deactivate_license(self) -> dict[str, Any]:
        self.licenses.deactivate()
        return self.licenses.status()

    def changelog(self, only_version: str | None = None) -> dict[str, Any]:
        """The CHANGELOG split into entries, newest first."""
        for candidate in (self.root / "CHANGELOG.md", bundle_dir() / "CHANGELOG.md"):
            if candidate.exists():
                text = candidate.read_text(encoding="utf-8")
                break
        else:
            return {"entries": [], "current": self.build_info.get("version")}
        entries: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in text.splitlines():
            if line.startswith("## "):
                current = {"version": line[3:].strip(), "lines": []}
                entries.append(current)
            elif current is not None:
                current["lines"].append(line)
        for e in entries:
            e["body"] = "\n".join(e.pop("lines")).strip()
        if only_version:
            entries = [e for e in entries if e["version"] == only_version]
        return {"entries": entries, "current": self.build_info.get("version")}

    # ----- manual trading -------------------------------------------------------------------
    def manual_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        rt = self.runtime
        if rt is None or self.state != "running":
            raise RuntimeError("start the bot first: manual orders use its exchange connection")
        side_text = str(payload.get("side", "")).lower()
        if side_text not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")

        def number(key: str) -> float | None:
            v = payload.get(key)
            if v in (None, ""):
                return None
            f = float(v)
            if f <= 0:
                raise ValueError(f"{key} must be a positive number")
            return f

        out = rt.engine.manual_order(
            Side(side_text), qty=number("qty"), quote_amount=number("quote_amount"),
            stop_loss=number("stop_loss"), take_profit=number("take_profit"),
            reason=str(payload.get("reason") or "manual order from the UI")[:200],
        )
        rt.notifier.send(f"manual {side_text.upper()} {out['order']['filled']:.6f} @ {out['price']:.2f}")
        return out

    def set_levels(self, payload: dict[str, Any]) -> dict[str, Any]:
        rt = self.runtime
        if rt is None or self.state != "running":
            raise RuntimeError("start the bot first")

        def number(key: str) -> float | None:
            v = payload.get(key)
            return None if v in (None, "") else float(v)

        return rt.engine.set_protective_levels(number("stop_loss"), number("take_profit"))

    def ai_status(self) -> dict[str, Any]:
        try:
            cfg = self.load_cfg()
        except Exception:  # noqa: BLE001
            return {"enabled": False}
        rt = self.runtime
        strategy = rt.engine.strategy if rt is not None else None
        info: dict[str, Any] = {
            "enabled": cfg.strategy.name == "ai",
            "key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "model": cfg.strategy.params.get("model", "claude-opus-5") if cfg.strategy.name == "ai" else None,
        }
        if strategy is not None and getattr(strategy, "name", "") == "ai":
            info.update({"calls": getattr(strategy, "calls", 0), "failures": getattr(strategy, "failures", 0),
                         "last_call": getattr(strategy, "last_call", {})})
        return info

    # ----- bot lifecycle ------------------------------------------------------------------
    def start(self, *, replay: str | None = None, replay_delay: float = 0.25, confirm_live: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.state in ("starting", "running", "stopping"):
                raise RuntimeError(f"bot is {self.state}")
            cfg = self.load_cfg()
            secrets = load_secrets()
            replay_file: Path | None = None
            if replay:
                replay_file = Path(replay)
                if not replay_file.is_absolute():
                    replay_file = self.root / replay_file
                if not replay_file.is_file():
                    raise FileNotFoundError(f"replay file not found: {replay_file}")
                mode = "paper"
                if cfg.exchange.live or secrets.has_api_keys and self.secrets_status()["live_trading_env"]:
                    log.info("replay requested: running in paper mode regardless of live switches")
            else:
                mode = resolve_mode(cfg, secrets)
                if mode == "live":
                    self.licenses.require_for_live()
                    if not confirm_live:
                        raise LiveSafetyError("Live mode needs explicit confirmation from the UI.")
            self._stop = threading.Event()
            self.state, self.error, self.mode = "starting", None, mode
            self.last_cycle, self.cycles, self.started_at = {}, 0, None
            self.replay_path = str(replay_file) if replay_file else None
            self._thread = threading.Thread(
                target=self._run, args=(cfg, secrets, mode, replay_file, replay_delay), daemon=True, name="bot-loop")
            self._thread.start()
        return self.status()

    def why_no_trades(self) -> dict[str, Any]:
        """Plain-language answer to 'the bot is running but not trading'."""
        # One snapshot: the bot keeps cycling, so the counts and their total must be read
        # together or they disagree by a candle.
        decisions = dict(self.decisions)
        rt, out = self.runtime, {"decisions": decisions, "recent": self.last_signals[-8:][::-1]}
        try:
            cfg = self.load_cfg()
        except Exception:  # noqa: BLE001
            return out
        last = self.last_cycle or {}
        signal = last.get("signal") or {}
        ind: dict[str, Any] = {}
        reason = signal.get("reason") or ""
        # "ema_fast=1 ema_slow=2 rsi=3 atr=4" appears in every technical reason string
        for part in reason.replace(";", " ").split():
            if "=" in part:
                k, _, v = part.partition("=")
                try:
                    ind[k] = float(v)
                except ValueError:
                    continue
        gap = None
        if ind.get("ema_fast") and ind.get("ema_slow"):
            gap = (ind["ema_fast"] / ind["ema_slow"] - 1) * 100
        risk = last.get("risk") or {}
        blockers: list[str] = []
        if self.state != "running":
            blockers.append("The bot is stopped. Press Start.")
        if last.get("kill_switch") or (self.kill_switch_path().exists() if cfg else False):
            blockers.append("The kill switch is on: no order will be placed.")
        if risk.get("halted"):
            blockers.append(f"Trading is halted for today: {risk.get('halt_reason')}")
        if risk.get("cooldown"):
            blockers.append(f"Cooldown after {risk.get('consecutive_losses')} losses in a row.")
        if signal.get("reason", "").startswith("warming up"):
            blockers.append(signal["reason"].capitalize() + ". It needs a full history before it decides.")
        waiting = (f"The strategy enters only when the fast EMA crosses above the slow EMA. "
                   f"They are {abs(gap):.2f}% apart ({'fast above' if gap and gap > 0 else 'fast below'} slow), "
                   f"so no crossover has happened yet.") if gap is not None else None
        tf = cfg.exchange.timeframe
        out.update({
            "state": self.state, "timeframe": tf, "strategy": cfg.strategy.name, "mode": cfg.mode,
            "indicators": ind, "ema_gap_pct": round(gap, 3) if gap is not None else None,
            "blockers": blockers, "waiting": waiting,
            "min_gap_minutes": round(cfg.risk.min_seconds_between_trades / 60),
            "candles_evaluated": sum(decisions.values()),
            "hints": [
                f"On {tf} candles an EMA crossover typically happens every few days. Fewer, longer candles mean fewer trades.",
                "A shorter timeframe (5m or 15m in Settings) trades far more often, with more noise and more fees.",
                "Aggressive mode enters on weaker signals and allows more trades per day.",
                "Demo replays historical candles quickly, so you can watch the bot trade without waiting.",
            ],
        })
        return out

    def _run(self, cfg: BotConfig, secrets: Any, mode: str, replay: Path | None, replay_delay: float) -> None:
        rt: Runtime | None = None
        self.decisions, self.last_signals = {}, []
        try:
            log_event(log, "ui_start", mode=mode, replay=str(replay) if replay else None, config=cfg.printable())
            rt = build_runtime(self.runtime_cfg(cfg), secrets, mode, str(replay) if replay else None, None)
            rt.replay_delay_seconds = replay_delay if replay else 0.0
            self.runtime = rt
            rt.engine.reconcile()
            with self._lock:
                self.state, self.started_at = "running", now_ms()
            rt.notifier.send(f"bot started in {mode} mode ({cfg.strategy.name}, {cfg.exchange.timeframe})")
            run_loop(rt, stop_event=self._stop)
            with self._lock:
                if self.state != "error":
                    self.state = "stopped"
        except Exception as exc:  # noqa: BLE001
            log.exception("bot thread failed")
            with self._lock:
                self.state, self.error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            if rt is not None:
                try:
                    rt.engine.persist()
                    rt.store.close()
                except Exception:  # noqa: BLE001
                    log.exception("persist on shutdown failed")
            self.runtime = None

    def stop(self, wait: float = 0.0) -> dict[str, Any]:
        with self._lock:
            if self.state in ("running", "starting"):
                self.state = "stopping"
            self._stop.set()
            thread = self._thread
        if wait and thread is not None:
            thread.join(wait)
        return self.status()

    def _update_event(self, info: dict[str, Any]) -> None:
        latest = info.get("latest") or {}
        if info.get("event") == "update_available":
            text = f"Update available: {latest.get('label')} (running {info.get('current')})"
        else:
            text = f"Update installed: {latest.get('label')}; restarting"
        log_event(log, info.get("event", "update"), level=logging.WARNING, detail=text, tag=latest.get("tag"))
        self.recent_alerts = (self.recent_alerts + [{"event": info.get("event"), "ts": None, "detail": text,
                                                     "label": latest.get("label"), "tag": latest.get("tag")}])[-30:]
        try:
            from bot.notify.notifier import Notifier

            Notifier(self.load_cfg().notify, load_secrets(), prefix="[BTC Bot] ").send(text)
        except Exception:  # noqa: BLE001
            pass

    def start_updater(self) -> None:
        try:
            cfg = self.load_cfg()
        except Exception:  # noqa: BLE001
            return
        if not cfg.update.enabled:
            return
        self.updater.repo = cfg.update.repo
        self.updater.cleanup_old()
        self.updater.start_background(
            cfg.update.check_interval_minutes,
            auto_install=lambda: self._auto_install_allowed(),
            before_restart=lambda: self.stop(wait=20.0),
        )

    def _auto_install_allowed(self) -> bool:
        try:
            return bool(self.load_cfg().update.auto_install) and self.state in ("stopped", "error")
        except Exception:  # noqa: BLE001
            return False

    def install_update(self) -> dict[str, Any]:
        """Manual install: stops the bot, swaps the executable, restarts, then this process quits."""
        result = self.updater.install(before_restart=lambda: self.stop(wait=20.0))
        threading.Thread(target=self.quit, daemon=True).start()
        return result

    def _on_event(self, item: dict[str, Any]) -> None:
        ev = item.get("event")
        if ev == "cycle":
            self.last_cycle = item
            self.cycles = int(item.get("cycle", self.cycles) or 0)
            decision, signal = item.get("decision") or {}, item.get("signal") or {}
            if signal:
                code = "order placed" if decision.get("intent") else str(decision.get("rejected") or "hold")
                if code == "hold" and signal.get("action") == "HOLD":
                    reason = str(signal.get("reason") or "")
                    code = ("warming up" if reason.startswith("warming up")
                            else "AI not called" if reason.startswith("AI not called")
                            else "no entry signal")
                self.decisions[code] = self.decisions.get(code, 0) + 1
                self.last_signals = (self.last_signals + [{
                    "ts": item.get("candle_time") or item.get("now"), "action": signal.get("action"),
                    "reason": signal.get("reason"), "code": code,
                }])[-20:]
        elif ev in ("trade_closed", "position_opened", "risk_event", "cycle_error", "protective_exit_blocked",
                    "replay_finished", "startup_refused", "shutdown", "reconcile", "manual_order",
                    "mode_changed", "levels_changed"):
            self.recent_alerts = (self.recent_alerts + [item])[-30:]

    # ----- kill switch --------------------------------------------------------------------
    def kill_switch_path(self) -> Path:
        return Path(self._abs(self.load_cfg().risk.kill_switch_file or "data/KILL_SWITCH"))

    def kill_switch(self, active: bool | None = None) -> bool:
        path = self.kill_switch_path()
        if active is True:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("created from the UI\n", encoding="utf-8")
            log_event(log, "kill_switch_on", level=logging.WARNING, path=str(path))
        elif active is False and path.exists():
            path.unlink()
            log_event(log, "kill_switch_off", path=str(path))
        return path.exists()

    # ----- status -------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        try:
            cfg = self.load_cfg()
            cfg_summary: dict[str, Any] = {
                "exchange": cfg.exchange.id, "symbol": cfg.exchange.symbol, "timeframe": cfg.exchange.timeframe,
                "live": cfg.exchange.live, "strategy": cfg.strategy.name, "initial_cash": cfg.paper.initial_cash,
                "mode": cfg.mode, "mode_matches": detect_mode(cfg.printable()) == cfg.mode,
                "risk_per_trade_pct": cfg.risk.risk_per_trade_pct, "max_daily_loss_pct": cfg.risk.max_daily_loss_pct,
                "max_position_pct": cfg.risk.max_position_pct,
            }
            config_error = None
        except Exception as exc:  # noqa: BLE001 - a broken config must still let the UI open
            cfg_summary, config_error = {}, f"{type(exc).__name__}: {exc}"
        secrets = self.secrets_status()
        live_possible = bool(cfg_summary.get("live") and secrets["live_trading_env"]
                             and secrets["api_key_set"] and secrets["api_secret_set"])
        last = self.last_cycle
        position = last.get("position")
        equity, cash = last.get("equity"), last.get("cash")
        rt = self.runtime
        if rt is not None and rt.engine is not None:
            try:
                position = rt.engine.position.to_dict() if rt.engine.position else None
                price_now = last.get("price") or last.get("close")
                if price_now:
                    acct = rt.engine.account(float(price_now))
                    equity, cash = round(acct.equity, 2), round(acct.cash, 2)
            except Exception:  # noqa: BLE001 - status must never fail
                pass
        try:
            kill = self.kill_switch_path().exists()
        except Exception:  # noqa: BLE001
            kill = False
        return {
            "version": VERSION,
            "state": self.state,
            "mode": self.mode,
            "error": self.error,
            "started_at": self.started_at,
            "cycles": self.cycles,
            "replay": self.replay_path,
            "config": cfg_summary,
            "config_error": config_error,
            "live_possible": live_possible,
            "kill_switch": kill,
            "secrets": secrets,
            "last_cycle": last,
            "position": position,
            "price": last.get("price") or last.get("close"),
            "equity": equity,
            "cash": cash,
            "risk": last.get("risk"),
            "signal": last.get("signal"),
            "decision": last.get("decision"),
            "alerts": self.recent_alerts[-10:],
            "backtest_state": self.backtest.get("state"),
            "research_state": self.research.get("state"),
            "download_state": self.download.get("state"),
            "services": {
                "telegram_commands": bool(self.telegram),
                "telegram_error": self.telegram.last_error if self.telegram else None,
                "watchdog": bool(self.watchdog),
                "watchdog_alerts": self.watchdog.alerts if self.watchdog else 0,
            },
            "license": self.license_status(),
            "trading_mode": (cfg_summary or {}).get("mode"),
            "trading_modes": mode_list(),
            "ai": self.ai_status(),
            "update": {**self.updater.status(), "build_info": self.build_info},
            "paths": {"root": str(self.root), "config": str(self.config_path), "db": str(self.db_path()),
                      "log": str(self.root / "data" / "bot.log"), "temporary": looks_temporary(self.root)},
            "now": now_ms(),
        }

    # ----- persisted data (read-only) ----------------------------------------------------
    def db_path(self) -> Path:
        try:
            p = Path(self.load_cfg().state.db_path)
        except Exception:  # noqa: BLE001
            p = Path("data/bot.sqlite")
        return p if p.is_absolute() else self.root / p

    def _read_conn(self) -> sqlite3.Connection | None:
        path = self.db_path()
        if not path.exists():
            return None
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def trades(self, limit: int = 500) -> list[dict[str, Any]]:
        conn = self._read_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute("SELECT * FROM trades ORDER BY exit_ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows][::-1]
        finally:
            conn.close()

    def equity(self, limit: int = 2000) -> list[dict[str, Any]]:
        conn = self._read_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute("SELECT ts, equity, cash, position_qty, price FROM equity ORDER BY ts").fetchall()
            return _downsample([dict(r) for r in rows], limit)
        finally:
            conn.close()

    def samples(self) -> list[str]:
        return [s["path"] for s in self.sample_details()]

    def sample_details(self) -> list[dict[str, Any]]:
        """Every CSV the app can backtest, with the period it actually covers."""
        out: list[dict[str, Any]] = []
        for folder in ("data/samples", "data/history"):
            d = self.root / folder
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.csv")):
                rel = Path(folder) / f.name
                item: dict[str, Any] = {"path": rel.as_posix(), "name": f.name}
                key = (str(f), f.stat().st_mtime)
                cached = self._chart_cache.get(("sample", key))
                if cached is None:
                    try:
                        df = load_csv(f)
                        cached = {"from": iso(int(df["ts"].iloc[0]))[:10], "to": iso(int(df["ts"].iloc[-1]))[:10],
                                  "candles": int(len(df)), "from_ms": int(df["ts"].iloc[0]), "to_ms": int(df["ts"].iloc[-1])}
                    except Exception as exc:  # noqa: BLE001 - a broken file must not hide the others
                        cached = {"error": f"{type(exc).__name__}: {exc}"}
                    self._chart_cache[("sample", key)] = cached
                out.append({**item, **cached})
        return out

    # ----- chart ---------------------------------------------------------------------------
    def _chart_rows(self, cfg: BotConfig, limit: int, timeframe: str) -> tuple[list[list[float]], str]:
        """Candles for the chart: the running bot's window when the timeframe matches, else a
        public fetch from the exchange (cached 20 s)."""
        rt = self.runtime
        if (rt is not None and self.state == "running" and rt.feed.latest_ts is not None
                and timeframe == rt.cfg.exchange.timeframe):
            df = rt.feed.candles().tail(limit)
            source = "replay" if rt.replay is not None else "feed"
            return df[["ts", "open", "high", "low", "close", "volume"]].values.tolist(), source
        key = f"{cfg.exchange.id}:{cfg.exchange.symbol}:{timeframe}:{limit}"
        with self._chart_lock:
            hit = self._chart_cache.get(key)
            if hit and now_ms() - hit["at"] < 20_000:
                return hit["rows"], "exchange"
        if self.market_factory is not None:
            market = self.market_factory(cfg)
        else:
            from bot.execution.live import LiveExchange

            quick = cfg.exchange.model_copy(update={"max_retries": 1, "backoff_base_seconds": 0.5, "backoff_max_seconds": 1.0})
            market = LiveExchange(quick, None)
        rows = market.fetch_ohlcv(cfg.exchange.symbol, timeframe, None, limit)
        with self._chart_lock:
            self._chart_cache[key] = {"at": now_ms(), "rows": rows}
        return rows, "exchange"

    def candles(self, limit: int = 300, timeframe: str | None = None) -> dict[str, Any]:
        cfg = self.load_cfg()
        limit = max(50, min(int(limit), 1000))
        rt = self.runtime
        bot_tf = rt.cfg.exchange.timeframe if (rt is not None and self.state == "running") else cfg.exchange.timeframe
        tf = (timeframe or bot_tf).strip()
        timeframe_to_ms(tf)  # raises ValueError on garbage
        rows, source = self._chart_rows(cfg, limit, tf)
        df = rows_to_frame(rows)
        indicators: dict[str, list[float | None]] = {}
        try:
            strategy = get_strategy(cfg.strategy.name, cfg.strategy.params)
            calc = getattr(strategy, "indicators", None)
            if calc is not None and len(df):
                ind = calc(df)
                for col in ind.columns:
                    indicators[col] = [None if v != v else round(float(v), 6) for v in ind[col].tolist()]
        except Exception as exc:  # noqa: BLE001 - the chart must still show candles
            log.warning("chart indicators failed: %s", exc)
        lo = int(df["ts"].iloc[0]) if len(df) else 0
        trades = [t for t in self.trades(limit=2000) if t["exit_ts"] >= lo]
        rt = self.runtime
        position = None
        if rt is not None and rt.engine is not None and rt.engine.position is not None:
            position = rt.engine.position.to_dict()
        elif self.state != "running":
            position = None
        last_price = self.last_cycle.get("price") if self.state == "running" else None
        return {
            "source": source, "symbol": cfg.exchange.symbol, "timeframe": tf, "bot_timeframe": bot_tf,
            "exchange": cfg.exchange.id, "candles": df.values.tolist(), "indicators": indicators,
            "trades": trades, "position": position, "last_price": last_price, "now": now_ms(),
        }

    # ----- research: parameter search and walk-forward ---------------------------------------
    def start_research(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._research_lock:
            if self.research.get("state") == "running":
                raise RuntimeError("a search is already running")
            self.research = {"state": "running", "params": params, "started_at": now_ms(),
                             "progress": {"done": 0, "total": 0, "phase": "starting"}}
            self._research_thread = threading.Thread(target=self._run_research, args=(params,), daemon=True,
                                                    name="research")
            self._research_thread.start()
        return dict(self.research)

    def _run_research(self, params: dict[str, Any]) -> None:
        from bot.backtest.optimize import DEFAULT_GRID, expand_grid, grid_search, walk_forward

        try:
            cfg = self.load_cfg()
            csv_path = params.get("csv")
            if not csv_path:
                raise ValueError("choose a data file to search over")
            path = Path(csv_path)
            df = load_csv(path if path.is_absolute() else self.root / path)
            since = parse_date_ms(params["from"]) if params.get("from") else None
            until = parse_date_ms(params["to"]) + 86_400_000 - 1 if params.get("to") else None
            if since is not None:
                df = df[df["ts"] >= since]
            if until is not None:
                df = df[df["ts"] <= until]
            df = df.reset_index(drop=True)
            if df.empty:
                raise ValueError("no candles in the requested range")
            sample = int(params.get("sample") or 60) or None
            objective = str(params.get("objective") or "return_over_drawdown")
            mode = str(params.get("mode") or "walk-forward")
            workers = int(params.get("workers") or min(4, os.cpu_count() or 2))

            def note(**kw: Any) -> None:
                with self._research_lock:
                    self.research["progress"] = {**self.research.get("progress", {}), **kw}

            if mode == "grid":
                def tick(done: int, total: int) -> None:
                    note(done=done, total=total, phase="testing parameter sets")

                ranked = grid_search(df, self.runtime_cfg(cfg), objective=objective, sample=sample,
                                     min_trades=int(params.get("min_trades") or 10), workers=workers,
                                     progress=tick)
                out = {"mode": "grid", "top": [c.to_dict() for c in ranked[:25]],
                       "tested": len(ranked), "objective": objective,
                       "note": "These are in-sample numbers: the search saw this whole period. "
                               "Run a walk-forward test before trusting them."}
            elif mode == "robustness":
                from bot.backtest.robustness import monte_carlo, sensitivity

                note(done=0, total=2, phase="backtesting the current settings")
                result = run_backtest(df, self.runtime_cfg(cfg))
                mc = monte_carlo(result.trades, result.equity,
                                 method=str(params.get("method") or "resample"),
                                 runs=int(params.get("runs") or 2000))

                def sens_tick(done: int, total: int) -> None:
                    note(done=done, total=total, phase="re-running with each setting changed")

                sens = sensitivity(df, self.runtime_cfg(cfg), objective=objective,
                                   min_trades=max(1, int(params.get("min_trades") or 10) // 2),
                                   workers=workers, progress=sens_tick)
                out = {"mode": "robustness", "objective": objective,
                       "backtest": result.metrics, "monte_carlo": mc.to_dict(),
                       "sensitivity": sens.to_dict(),
                       "note": "The re-deal asks what the same trades would have done in another "
                               "order; the sweep asks whether the settings still work when nudged."}
            else:
                def wf_tick(phase: str, fold: int, total: int) -> None:
                    note(done=fold, total=total, phase=f"block {fold} of {total}: {phase}")

                out = walk_forward(df, self.runtime_cfg(cfg), folds=int(params.get("folds") or 4),
                                   train_ratio=float(params.get("train_ratio") or 0.7), objective=objective,
                                   sample=sample, min_trades=max(1, int(params.get("min_trades") or 10) // 2),
                                   workers=workers, progress=wf_tick)
                out["mode"] = "walk-forward"
            with self._research_lock:
                self.research = {"state": "done", "params": params, "finished_at": now_ms(),
                                 "candles": int(len(df)), "source": Path(csv_path).name, **out}
        except Exception as exc:  # noqa: BLE001
            log.exception("research failed")
            with self._research_lock:
                self.research = {"state": "error", "params": params, "error": f"{type(exc).__name__}: {exc}"}

    def research_status(self) -> dict[str, Any]:
        with self._research_lock:
            return dict(self.research)

    def apply_research(self, params: dict[str, Any]) -> dict[str, Any]:
        """Write a parameter set found by the search into config.yaml."""
        if not params:
            raise ValueError("no parameters to apply")
        data = self.load_cfg().printable()
        data["strategy"]["params"] = {**data["strategy"].get("params", {}), **params}
        cfg = self.save_config(data)
        log_event(log, "params_applied", level=logging.WARNING, detail=f"Strategy parameters updated: {params}")
        return {"config": cfg.printable(), "applied": params}

    # ----- history download -------------------------------------------------------------------
    def start_download(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._download_lock:
            if self.download.get("state") == "running":
                raise RuntimeError("a download is already running")
            self.download = {"state": "running", "params": params, "started_at": now_ms(), "candles": 0}
        threading.Thread(target=self._run_download, args=(params,), daemon=True, name="download").start()
        return dict(self.download)

    def _run_download(self, params: dict[str, Any]) -> None:
        try:
            cfg = self.load_cfg()
            symbol = str(params.get("symbol") or cfg.exchange.symbol)
            timeframe = str(params.get("timeframe") or cfg.exchange.timeframe)
            timeframe_to_ms(timeframe)
            since = parse_date_ms(params["from"]) if params.get("from") else now_ms() - 365 * 86_400_000
            until = parse_date_ms(params["to"]) + 86_400_000 - 1 if params.get("to") else now_ms()
            if self.market_factory is not None:
                market = self.market_factory(cfg)
            else:
                from bot.execution.live import LiveExchange

                market = LiveExchange(cfg.exchange, None)
            cache = cache_path(self.root / "data" / "history", cfg.exchange.id, symbol, timeframe)
            df = download_ohlcv(market, symbol=symbol, timeframe=timeframe, since_ms=since, until_ms=until,
                                cache=cache)
            with self._download_lock:
                self.download = {"state": "done", "params": params, "file": str(cache.relative_to(self.root)),
                                 "candles": int(len(df)), "from": iso(int(df["ts"].iloc[0])),
                                 "to": iso(int(df["ts"].iloc[-1])), "finished_at": now_ms()}
            self._chart_cache.clear()
        except Exception as exc:  # noqa: BLE001
            log.exception("history download failed")
            with self._download_lock:
                self.download = {"state": "error", "params": params, "error": f"{type(exc).__name__}: {exc}"}

    def download_status(self) -> dict[str, Any]:
        with self._download_lock:
            return dict(self.download)

    # ----- analytics and exports ----------------------------------------------------------------
    def analytics(self) -> dict[str, Any]:
        conn = self._read_conn()
        trades = []
        if conn is not None:
            from bot.state.store import StateStore

            store = StateStore(self.db_path())
            trades = store.trades()
            store.close()
        return trade_analytics(trades, self.equity(limit=100000))

    def export_csv(self, what: str) -> tuple[str, str]:
        from bot.state.store import StateStore

        store = StateStore(self.db_path())
        try:
            if what == "trades":
                return trades_csv(store.trades()), "trades.csv"
            if what == "equity":
                return equity_csv(store.equity_curve()), "equity.csv"
        finally:
            store.close()
        raise ValueError(f"unknown export {what!r}")

    # ----- telegram control and the watchdog ------------------------------------------------------
    def _telegram_handlers(self) -> dict[str, Any]:
        def status() -> str:
            s = self.status()
            pos = s.get("position")
            lines = [f"{s['state']} · {s.get('mode') or '—'} · {s['config'].get('symbol', '?')} "
                     f"{s['config'].get('timeframe', '')} · {s.get('trading_mode')}"]
            if s.get("equity") is not None:
                lines.append(f"Equity {s['equity']:,.2f} · cash {s.get('cash', 0):,.2f} · price {s.get('price') or 0:,.2f}")
            lines.append(f"Position: {pos['qty']:.6f} @ {pos['entry_price']:,.2f} (stop {pos['stop_loss']}, "
                         f"target {pos['take_profit']})" if pos else "Position: flat")
            risk = s.get("risk") or {}
            lines.append(f"Risk: halted={risk.get('halted')} cooldown={risk.get('cooldown')} "
                         f"kill_switch={s.get('kill_switch')} today={risk.get('daily_drawdown_pct')}%")
            return "\n".join(lines)

        def pnl() -> str:
            a = self.analytics()
            if not a["trades"]:
                return "No closed trades yet."
            return (f"{a['trades']} trades · realised {a['pnl']:+,.2f} · fees {a['fees']:,.2f}\n"
                    f"Win rate {a['win_rate_pct']}% · expectancy {a['expectancy']:+,.2f} per trade\n"
                    f"Best {a['best']:+,.2f} · worst {a['worst']:+,.2f}")

        def position() -> str:
            s = self.status()
            p = s.get("position")
            if not p:
                return "No open position."
            price = s.get("price") or p["entry_price"]
            return (f"{p['qty']:.6f} @ {p['entry_price']:,.2f}\nNow {price:,.2f} "
                    f"({(price / p['entry_price'] - 1) * 100:+.2f}%)\n"
                    f"Stop {p['stop_loss']} · target {p['take_profit']}\n"
                    f"Unrealised {(price - p['entry_price']) * p['qty']:+,.2f}")

        def why() -> str:
            w = self.why_no_trades()
            parts = w.get("blockers") or []
            if w.get("waiting"):
                parts.append(w["waiting"])
            counts = ", ".join(f"{k}: {v}" for k, v in sorted(w.get("decisions", {}).items(), key=lambda x: -x[1])[:4])
            return "\n".join(parts + [f"Decisions so far: {counts}" if counts else ""]).strip() or "Nothing to report."

        def stop_bot() -> str:
            self.stop(wait=10)
            return "Stopped. The app is still running; send /start to trade again."

        def start_bot() -> str:
            if self.state == "running":
                return "Already running."
            self.start()
            return f"Started in {self.mode or 'paper'} mode."

        def kill_on() -> str:
            self.kill_switch(True)
            return "Kill switch ON. No order will be placed, by the bot or by hand."

        def kill_off() -> str:
            self.kill_switch(False)
            return "Kill switch off. Trading can resume."

        return {"status": status, "pnl": pnl, "position": position, "why": why, "stop": stop_bot,
                "start": start_bot, "kill": kill_on, "unkill": kill_off}

    def start_services(self) -> None:
        """Telegram commands, the watchdog and the daily report."""
        try:
            cfg = self.load_cfg()
        except Exception:  # noqa: BLE001
            return
        secrets = load_secrets()
        if (cfg.notify.telegram_commands and secrets.telegram_bot_token and secrets.telegram_chat_id
                and self.telegram is None):
            self.telegram = TelegramControl(secrets.telegram_bot_token, secrets.telegram_chat_id,
                                            self._telegram_handlers())
            self.telegram.start()
        if cfg.notify.watchdog_minutes > 0 and self.watchdog is None:
            self.watchdog = Watchdog(
                is_running=lambda: self.state == "running",
                last_cycle_ms=lambda: self._last_cycle_ms(),
                notify=lambda text: self._notify(f"WATCHDOG {text}"),
                stall_seconds=cfg.notify.watchdog_minutes * 60.0,
            )
            self.watchdog.start()
        if self._report_thread is None:
            self._report_thread = threading.Thread(target=self._report_loop, daemon=True, name="daily-report")
            self._report_thread.start()

    def _last_cycle_ms(self) -> int | None:
        ts = (self.last_cycle or {}).get("ts")
        if not ts:
            return None
        try:
            from datetime import datetime

            return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000)
        except Exception:  # noqa: BLE001
            return None

    def _notify(self, text: str) -> None:
        try:
            Notifier(self.load_cfg().notify, load_secrets(), prefix="[BTC Bot] ").send(text)
        except Exception:  # noqa: BLE001
            log.warning("notification failed", exc_info=True)

    def _report_loop(self) -> None:
        while not self.quit_event.wait(300):
            try:
                cfg = self.load_cfg()
                hour = cfg.notify.daily_report_hour_utc
                if hour < 0:
                    continue
                now = datetime.now(tz=timezone.utc)
                day = now.strftime("%Y-%m-%d")
                if now.hour == hour and self._last_report_day != day:
                    self._last_report_day = day
                    from bot.state.store import StateStore

                    store = StateStore(self.db_path())
                    text = daily_summary(store.trades(), store.equity_curve(), day=day,
                                         symbol=cfg.exchange.symbol, mode=cfg.mode)
                    store.close()
                    self._notify(text)
                    log_event(log, "daily_report", detail="daily report sent")
            except Exception:  # noqa: BLE001
                log.exception("daily report failed")

    # ----- backtests ----------------------------------------------------------------------
    def start_backtest(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._bt_lock:
            if self.backtest.get("state") == "running":
                raise RuntimeError("a backtest is already running")
            self.backtest = {"state": "running", "params": params, "started_at": now_ms()}
            self._bt_thread = threading.Thread(target=self._run_backtest, args=(params,), daemon=True, name="backtest")
            self._bt_thread.start()
        return dict(self.backtest)

    def _run_backtest(self, params: dict[str, Any]) -> None:
        try:
            cfg = self.load_cfg()
            since = parse_date_ms(params["from"]) if params.get("from") else None
            until = parse_date_ms(params["to"]) + 86_400_000 - 1 if params.get("to") else None
            csv = params.get("csv")
            if csv:
                path = Path(csv)
                if not path.is_absolute():
                    path = self.root / path
                df = load_csv(path)
                source = str(path.name)
            else:
                if since is None or until is None:
                    raise ValueError("choose a CSV file or give both dates to download candles")
                from bot.execution.live import LiveExchange

                market = LiveExchange(cfg.exchange)
                cache = cache_path(self.root / "data" / "history", cfg.exchange.id, cfg.exchange.symbol, cfg.exchange.timeframe)
                df = download_ohlcv(market, symbol=cfg.exchange.symbol, timeframe=cfg.exchange.timeframe,
                                    since_ms=since, until_ms=until, cache=cache)
                source = f"{cfg.exchange.id} download"
            have_from, have_to = (iso(int(df["ts"].iloc[0])), iso(int(df["ts"].iloc[-1]))) if len(df) else (None, None)
            if since is not None:
                df = df[df["ts"] >= since]
            if until is not None:
                df = df[df["ts"] <= until]
            df = df.reset_index(drop=True)
            if df.empty:
                asked = f"{params.get('from') or 'the start'} to {params.get('to') or 'the end'}"
                raise ValueError(
                    f"{source} has no candles between {asked}. This file covers "
                    f"{(have_from or '?')[:10]} to {(have_to or '?')[:10]} — pick dates inside that period, "
                    f"or clear both date fields to use the whole file.")
            strategy_params = dict(cfg.strategy.params)
            strategy_params.update(params.get("params") or {})
            cash = float(params["cash"]) if params.get("cash") else None
            try:
                result = run_backtest(df, self.runtime_cfg(cfg), strategy_name=params.get("strategy") or None,
                                      params=strategy_params, initial_cash=cash)
            except ValueError as exc:
                if "need at least" in str(exc):
                    raise ValueError(
                        f"{exc}. The selected period only has {len(df)} candles: choose a longer range, "
                        f"or a shorter timeframe in Settings.") from exc
                raise
            curve = [{"ts": int(r.ts), "equity": float(r.equity), "position_qty": float(r.position_qty)}
                     for r in result.equity.itertuples()]
            with self._bt_lock:
                self.backtest = {
                    "state": "done", "params": params, "source": source, "candles": int(len(df)),
                    "metrics": result.metrics, "strategy": result.strategy, "config": result.config,
                    "trades": [t.to_dict() for t in result.trades][-200:],
                    "equity": _downsample(curve, 1500),
                    "report": format_report(result, show_trades=10),
                    "finished_at": now_ms(),
                }
        except Exception as exc:  # noqa: BLE001
            log.exception("backtest failed")
            with self._bt_lock:
                self.backtest = {"state": "error", "params": params, "error": f"{type(exc).__name__}: {exc}"}

    def backtest_status(self) -> dict[str, Any]:
        with self._bt_lock:
            return dict(self.backtest)

    # ----- shutdown -----------------------------------------------------------------------
    def quit(self, wait: float = 15.0) -> None:
        self.stop(wait=wait)
        if self.telegram:
            self.telegram.stop()
        if self.watchdog:
            self.watchdog.stop()
        self.updater.stop()
        self.quit_event.set()
