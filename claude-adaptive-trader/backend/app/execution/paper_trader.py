"""Continuous paper trading with a local virtual broker.

Two data modes, always labelled:

* ``live``   - Binance public market data as it arrives (WebSocket/REST into the cache). Each
  newly closed candle goes through the trading core exactly as in a backtest.
* ``replay`` - stored historical candles played back at a chosen speed. Useful where Binance
  cannot be reached, and clearly marked as a replay everywhere it is shown.

Persistence and recovery. Every processed candle is one database transaction: the core's
orders, fills, positions, trades, decisions and equity, plus the session's cash, breakers and
the per-pair cursor. A crash rolls the candle back and it is processed again on restart, so
nothing is lost or doubled; client order ids are deterministic and unique, so a re-processed
signal cannot create a second order. After downtime the missed candles are replayed for open
positions (stops and targets would have rested at an exchange), but no new entry is taken on a
candle that is not the latest one.

A paper result is simulated. It is never real money and is never presented as such.
"""
from __future__ import annotations

import secrets
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.core.clock import now_ms, parse_ts
from app.core.errors import Conflict, DataUnavailable, InvalidRequest, NotFound
from app.core.events import EventBus
from app.core.timeframes import last_closed_open, tf_ms, validate_timeframe
from app.data.binance_client import assumed_rules
from app.data.market_data import MarketDataService
from app.execution.core import PairState, TradingCore
from app.execution.models import Costs, Order, Position
from app.execution.portfolio import Portfolio
from app.execution.recorders import PaperRecorder
from app.monitoring.health import HealthMonitor
from app.monitoring.logging import get_logger, log_event
from app.risk import circuit_breakers as cb
from app.risk.risk_manager import EngineHealth, RiskManager
from app.strategies.base import Strategy
from app.strategies.pipeline import context, prepare, signals
from app.strategies.registry import STRATEGIES, build
from app.strategies.selector import FixedSelector, Selector
from app.storage.database import Database, from_json, to_json

log = get_logger("paper")

WINDOW = 3000            # candles of history behind every live decision
KILL_KEY = "kill_switch"


class PaperStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_mode: Literal["live", "replay"] = "live"
    strategy_mode: Literal["adaptive", "forward_test"] = "adaptive"
    strategy: str | None = None
    pairs: list[str] | None = None
    timeframe: str | None = None
    exchange: str = "binance"
    replay_start: int | str | None = None
    replay_end: int | str | None = None
    replay_speed: float | None = Field(None, gt=0, le=10_000)
    initial_cash: float | None = Field(None, gt=0, le=1e9)


StrategyParamsFn = Callable[[str], dict[str, Any]]
SelectorFactory = Callable[[dict[str, Any], dict[str, Strategy]], Selector]


def _session_id() -> str:
    return "ps-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class PaperTrader:
    def __init__(self, settings: Callable[[], Settings], db: Database, market: MarketDataService,
                 health: HealthMonitor, bus: EventBus | None = None, *,
                 strategy_params: StrategyParamsFn | None = None,
                 selector_factory: SelectorFactory | None = None, clock: Callable[[], int] = now_ms,
                 autostart: bool = True) -> None:
        self.autostart = autostart          # tests drive tick() themselves
        self._settings = settings
        self.db = db
        self.market = market
        self.health_monitor = health
        self.bus = bus
        self.strategy_params = strategy_params or (lambda name: {})
        self.selector_factory = selector_factory
        self.clock = clock
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.session: dict[str, Any] | None = None
        self.core: TradingCore | None = None
        self.strategies: dict[str, Strategy] = {}
        self._replay_states: dict[str, PairState] = {}
        self.pair_status: dict[str, dict[str, Any]] = {}
        self.last_error: str | None = None

    @property
    def settings(self) -> Settings:
        return self._settings()

    # ----- kill switch (global, persistent) -------------------------------------------------------
    def kill_switch(self) -> dict[str, Any]:
        value = self.db.get_state(KILL_KEY)
        if isinstance(value, dict):
            return value
        on = bool(self.settings.risk.kill_switch)
        return {"on": on, "by": "config" if on else None, "at": None, "reason": "set in config.yaml" if on else None}

    def set_kill_switch(self, on: bool, *, actor: str, reason: str) -> dict[str, Any]:
        record = {"on": bool(on), "by": actor, "at": self.clock(), "reason": reason}
        self.db.set_state(KILL_KEY, record)
        cancelled = 0
        with self._lock:
            if on and self.core is not None:
                with self.db.transaction():
                    cancelled = self.core.cancel_pending_entries("kill switch turned on")
        log_event(log, 30 if on else 20, f"kill switch {'ON' if on else 'off'}", actor=actor, reason=reason,
                  cancelled_entries=cancelled)
        if self.bus:
            self.bus.publish({"type": "paper", "event": "kill_switch", "data": record})
        return {**record, "cancelled_entries": cancelled}

    # ----- engine health for risk check 14 ---------------------------------------------------------------
    def engine_health(self) -> EngineHealth:
        problems = []
        if self.kill_switch().get("on"):
            problems.append("kill switch is on: no new entries")
        critical = self.health_monitor.critical()
        if critical:
            problems.append(f"critical error not cleared: {critical.get('message')}")
        s = self.session
        if s is None or s["status"] != "running":
            problems.append(f"paper engine is {s['status'] if s else 'not running'}")
        if s is not None and s["data_mode"] == "live":
            st = self.market.status()
            if st["status"] == "error":
                problems.append("market data feed unavailable")
        return EngineHealth(not problems, tuple(problems))

    # ----- lifecycle --------------------------------------------------------------------------------------
    def start(self, params: PaperStart) -> dict[str, Any]:
        with self._lock:
            if self.session and self.session["status"] in {"running", "paused"}:
                raise Conflict(f"session {self.session['id']} is {self.session['status']}; stop it before starting another")
            s = self.settings
            pairs = params.pairs or (s.exchange.pairs if params.exchange == "binance" else [])
            if not pairs:
                raise InvalidRequest("choose at least one pair")
            timeframe = validate_timeframe(params.timeframe or s.exchange.signal_timeframe)
            confirm = [tf for tf in s.exchange.confirm_timeframes if tf_ms(tf) > tf_ms(timeframe)
                       and tf_ms(tf) % tf_ms(timeframe) == 0] if timeframe == s.exchange.signal_timeframe else \
                self._confirm_for(timeframe)
            if params.data_mode == "live" and params.exchange != "binance":
                raise InvalidRequest("live paper trading uses Binance public data only")
            if params.strategy_mode == "forward_test":
                if not params.strategy or params.strategy not in STRATEGIES:
                    raise InvalidRequest(f"forward test needs a strategy: one of {', '.join(STRATEGIES)}")
            elif self.selector_factory is None:
                raise InvalidRequest("adaptive mode is not available yet; use a forward test of one strategy")
            replay_start = replay_end = None
            if params.data_mode == "replay":
                replay_start, replay_end = self._replay_window(params, pairs, timeframe)
            cash = params.initial_cash or s.portfolio.initial_cash
            now = self.clock()
            sid = _session_id()
            strategy_params = {name: self.strategy_params(name) for name in STRATEGIES}
            session = {
                "id": sid, "created_at": now, "updated_at": now, "status": "running", "data_mode": params.data_mode,
                "strategy_mode": params.strategy_mode, "forward_strategy": params.strategy, "pairs": pairs,
                "timeframe": timeframe, "exchange": params.exchange, "initial_cash": cash, "cash": cash,
                "peak_equity": cash, "day_start_equity": cash, "day_start_ts": now - now % 86_400_000,
                "clock_ts": replay_start if params.data_mode == "replay" else now,
                "replay_start_ts": replay_start, "replay_end_ts": replay_end,
                "replay_speed": params.replay_speed or s.paper.replay_bars_per_second,
                "config_snapshot": s.printable(), "started_at": now, "confirm_timeframes": confirm,
                "breaker_state": cb.new_state(cash, replay_start or now).to_dict(), "strategy_params": strategy_params,
            }
            self.db.execute(
                "INSERT INTO paper_sessions (id, created_at, updated_at, status, data_mode, strategy_mode,"
                " forward_strategy, pairs, timeframe, exchange, initial_cash, cash, peak_equity, day_start_equity,"
                " day_start_ts, clock_ts, replay_start_ts, replay_end_ts, replay_speed, config_snapshot, started_at,"
                " confirm_timeframes, breaker_state, strategy_params) VALUES"
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, now, now, "running", params.data_mode, params.strategy_mode, params.strategy, to_json(pairs),
                 timeframe, params.exchange, cash, cash, cash, cash, session["day_start_ts"], session["clock_ts"],
                 replay_start, replay_end, session["replay_speed"], to_json(session["config_snapshot"]), now,
                 to_json(confirm), to_json(session["breaker_state"]), to_json(strategy_params)))
            # Cursors: live sessions start at the latest closed candle (no trading on history);
            # replays one candle before the replay start.
            step = tf_ms(timeframe)
            for pair in pairs:
                if params.data_mode == "live":
                    latest = self.market.store.latest_ts(params.exchange, pair, timeframe)
                    cursor = latest if latest is not None else last_closed_open(now, timeframe) - step
                else:
                    cursor = int(replay_start) - step
                self.db.execute("INSERT INTO paper_cursors (session_id, pair, last_processed_ts) VALUES (?, ?, ?)",
                                (sid, pair, cursor))
            self._load(sid)
            log_event(log, 20, f"paper session {sid} started", data_mode=params.data_mode,
                      strategy_mode=params.strategy_mode, strategy=params.strategy, pairs=pairs, timeframe=timeframe)
            self._spawn()
            if params.data_mode == "live":
                self._warm_history(pairs, timeframe, confirm)
            return self.status()

    def _confirm_for(self, timeframe: str) -> list[str]:
        """Two higher confirmation timeframes for a non-default signal timeframe (e.g. 1h -> 4h, 1d)."""
        base = tf_ms(timeframe)
        options = [tf for tf in ("1h", "4h", "1d") if tf_ms(tf) > base and tf_ms(tf) % base == 0]
        return options[:2]

    def _replay_window(self, params: PaperStart, pairs: list[str], timeframe: str) -> tuple[int, int]:
        step = tf_ms(timeframe)
        firsts, lasts = [], []
        for pair in pairs:
            series = self.market.history(pair, timeframe, exchange=params.exchange)
            if series.empty:
                raise DataUnavailable(f"no stored {params.exchange} {pair} {timeframe} candles to replay")
            firsts.append(int(series["ts"].iloc[0]))
            lasts.append(int(series["ts"].iloc[-1]))
        start = parse_ts(params.replay_start) if params.replay_start is not None else None
        end = parse_ts(params.replay_end) if params.replay_end is not None else None
        common_first, common_last = max(firsts), min(lasts)
        warm = (WINDOW // 3) * step
        start = max(start or (common_first + warm), common_first)
        end = min(end or common_last, common_last)
        start, end = start - start % step, end - end % step
        if end <= start:
            raise InvalidRequest("the replay window is empty for the chosen pairs")
        return start, end

    def _warm_history(self, pairs: list[str], timeframe: str, confirm: list[str]) -> None:
        def work() -> None:
            for pair in pairs:
                try:
                    self.market.sync(pair, timeframe, start_ms=self.clock() - (WINDOW + 10) * tf_ms(timeframe))
                except Exception as exc:  # noqa: BLE001 - reported in status, retried by the poller
                    self.last_error = f"history download for {pair} failed: {exc}"
        threading.Thread(target=work, name="paper-history", daemon=True).start()

    def pause(self) -> dict[str, Any]:
        return self._set_status("paused", "paused by user")

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if not self.session:
                raise NotFound("no paper session")
            if self.session["status"] == "stopped":
                raise Conflict("this session has stopped; start a new one")
            self._set_status("running", "resumed by user")
            self._spawn()
            return self.status()

    def stop(self, note: str = "stopped by user") -> dict[str, Any]:
        with self._lock:
            if not self.session:
                raise NotFound("no paper session")
            self._set_status("stopped", note)
            self.db.execute("UPDATE paper_sessions SET stopped_at = ? WHERE id = ?", (self.clock(), self.session["id"]))
        self._join()
        return self.status()

    def close_all(self, reason: str) -> dict[str, Any]:
        with self._lock:
            if not self.core or not self.session:
                raise NotFound("no paper session")
            with self.db.transaction():
                n = self.core.close_all(self.session.get("clock_ts") or self.clock(), f"manual close: {reason}")
            log_event(log, 30, f"close all requested for {n} position(s)", reason=reason)
            return {"orders": n, **self.status()}

    def reset_drawdown(self, note: str) -> dict[str, Any]:
        with self._lock:
            if not self.core or not self.session:
                raise NotFound("no paper session")
            equity = self.core.portfolio.equity(self.core.marks)
            event = cb.reset_drawdown(self.core.breakers, equity, self.clock(), "user", note)
            self._save_session()
            log_event(log, 30, "drawdown breaker reset", note=note, equity=equity)
            return {"event": event, **self.status()}

    def _set_status(self, status: str, note: str) -> dict[str, Any]:
        with self._lock:
            if not self.session:
                raise NotFound("no paper session")
            self.session["status"] = status
            self.db.execute("UPDATE paper_sessions SET status = ?, updated_at = ?, note = ? WHERE id = ?",
                            (status, self.clock(), note, self.session["id"]))
            log_event(log, 20, f"paper session {self.session['id']} {status}", note=note)
            if self.bus:
                self.bus.publish({"type": "paper", "event": "status", "data": {"status": status}})
            self._wake.set()
            return self.status()

    # ----- thread --------------------------------------------------------------------------------------------
    def _spawn(self) -> None:
        if not self.autostart:
            return
        if self._thread and self._thread.is_alive():
            self._wake.set()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="paper-trader", daemon=True)
        self._thread.start()

    def _join(self) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=15)

    def shutdown(self) -> None:
        self._join()

    def _run(self) -> None:
        while not self._stop.is_set():
            session = self.session
            if session is None or session["status"] in {"stopped", "error"}:
                return
            if session["status"] == "paused":
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            try:
                progressed = self.tick()
            except Exception as exc:  # noqa: BLE001 - any failure is a critical, recorded error
                self._fail(exc)
                return
            if session["data_mode"] == "replay":
                if not progressed:
                    self.stop("replay finished")
                    return
                delay = 1.0 / float(session.get("replay_speed") or 20.0)
                if delay > 0.0005:
                    self._wake.wait(delay)
                    self._wake.clear()
            else:
                self._wake.wait(self.settings.paper.poll_seconds)
                self._wake.clear()

    def _fail(self, exc: BaseException) -> None:
        tb = traceback.format_exc()[-6000:]
        with self._lock:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self.session:
                self.session["status"] = "error"
                self.db.execute("UPDATE paper_sessions SET status = 'error', last_error = ?, updated_at = ? WHERE id = ?",
                                (self.last_error, self.clock(), self.session["id"]))
            self.health_monitor.raise_critical("paper_trader", self.last_error, traceback=tb)
            # Rebuild from the database so memory matches the last committed state.
            if self.session:
                self._load(self.session["id"])
                self.session["status"] = "error"

    # ----- processing ---------------------------------------------------------------------------------------
    def tick(self) -> bool:
        """Process every candle that has closed since the last tick. Returns True if any was processed."""
        with self._lock:
            s = self.session
            if s is None or s["status"] != "running":
                return False
            if s["data_mode"] == "replay":
                return self._replay_step()
            progressed = False
            for pair in s["pairs"]:
                progressed |= self._live_pair(pair)
            return progressed

    def _cursor(self, pair: str) -> int:
        return int(self.db.scalar("SELECT last_processed_ts FROM paper_cursors WHERE session_id = ? AND pair = ?",
                                  (self.session["id"], pair)))

    def _live_pair(self, pair: str) -> bool:
        s = self.session
        tf = s["timeframe"]
        now = self.clock()
        cursor = self._cursor(pair)
        newest = last_closed_open(now, tf)
        new = self.market.store.load(s["exchange"], pair, tf, start=cursor + 1, end=newest)
        if new.empty:
            self.pair_status.setdefault(pair, {})["waiting"] = "no new closed candle yet"
            return False
        for ts in new["ts"].astype("int64").tolist():
            history = self.market.store.load(s["exchange"], pair, tf, end=ts, limit=WINDOW, newest=True)
            ps = self._pair_state(pair, history)
            self._process(ps, [(ps, len(ps.ts) - 1)], {pair: ts}, clock_ts=now)
        return True

    def _replay_step(self) -> bool:
        s = self.session
        tf = s["timeframe"]
        step = tf_ms(tf)
        if not self._replay_states:
            for pair in s["pairs"]:
                start = int(s["replay_start_ts"]) - WINDOW * step
                hist = self.market.history(pair, tf, start=start, end=int(s["replay_end_ts"]), exchange=s["exchange"])
                self._replay_states[pair] = self._pair_state(pair, hist)
        cursors = {pair: self._cursor(pair) for pair in s["pairs"]}
        nxt = None
        for pair, ps in self._replay_states.items():
            later = ps.ts[ps.ts > cursors[pair]]
            later = later[later <= int(s["replay_end_ts"])]
            if len(later):
                nxt = int(later[0]) if nxt is None else min(nxt, int(later[0]))
        if nxt is None:
            return False
        items = []
        for ps in self._replay_states.values():
            i = ps.index.get(nxt)
            if i is not None:
                items.append((ps, i))
        self._process(items[0][0], items, {ps.pair: nxt for ps, _ in items}, clock_ts=nxt + step)
        return True

    def _pair_state(self, pair: str, candles: pd.DataFrame) -> PairState:
        s = self.session
        settings = self.settings
        if candles.empty:
            raise DataUnavailable(f"no candles for {pair}")
        view = prepare(candles, pair, s["timeframe"], s["confirm_timeframes"], settings)
        ctx = context(settings, s["timeframe"], s["confirm_timeframes"])
        sigs = {name: signals(view, strat, ctx) for name, strat in self.strategies.items()}
        if s["exchange"] == "binance":
            rules = self.market.rules(pair).to_dict()
        else:
            rules = assumed_rules(pair).to_dict()
        return PairState(pair, view, sigs, rules)

    def _process(self, _ps: PairState, items: list[tuple[PairState, int]], cursors: dict[str, int], *,
                 clock_ts: int) -> None:
        s = self.session
        core = self.core
        assert core is not None
        with self.db.transaction():
            core.step(items)
            core.portfolio.check_invariants(core.marks)
            for pair, ts in cursors.items():
                self.db.execute("UPDATE paper_cursors SET last_processed_ts = ? WHERE session_id = ? AND pair = ?",
                                (ts, s["id"], pair))
            s["clock_ts"] = clock_ts
            self._save_session()
        for ps, i in items:
            reg = ps.view.regime.iloc[i]
            self.pair_status[ps.pair] = {
                "last_candle_ts": int(ps.ts[i]), "regime": str(reg["regime"]),
                "regime_confidence": float(reg["regime_confidence"]), "regime_reason": str(reg["regime_reason"]),
                "close": float(ps.close[i]), "processed_at": self.clock(), "waiting": None,
            }
        if self.bus:
            self.bus.publish({"type": "paper", "event": "tick", "data": {"cursors": cursors}})

    def _save_session(self) -> None:
        s, core = self.session, self.core
        if not s or not core:
            return
        br = core.breakers
        self.db.execute(
            "UPDATE paper_sessions SET cash = ?, peak_equity = ?, day_start_equity = ?, day_start_ts = ?,"
            " clock_ts = ?, breaker_state = ?, updated_at = ? WHERE id = ?",
            (core.portfolio.cash, br.peak_equity, br.day_start_equity, br.day_start_ts, s.get("clock_ts"),
             to_json(br.to_dict()), self.clock(), s["id"]))

    # ----- loading and recovery -----------------------------------------------------------------------------
    def restore(self) -> None:
        row = self.db.query_one(
            "SELECT id, status FROM paper_sessions WHERE archived = 0 AND status IN ('running', 'paused', 'error')"
            " ORDER BY created_at DESC LIMIT 1")
        if not row:
            return
        with self._lock:
            try:
                self._load(row["id"])
            except Exception as exc:  # noqa: BLE001 - never block startup; report instead
                self.last_error = f"could not restore session {row['id']}: {exc}"
                self.health_monitor.raise_critical("paper_trader", self.last_error)
                self.db.execute("UPDATE paper_sessions SET status = 'error', last_error = ? WHERE id = ?",
                                (self.last_error, row["id"]))
                return
            assert self.session is not None
            if row["status"] == "running" and self.settings.paper.auto_resume:
                log_event(log, 20, f"paper session {row['id']} restored after restart and resumed",
                          positions=len(self.core.portfolio.positions) if self.core else 0)
                self._spawn()
            elif row["status"] == "running":
                self.session["status"] = "paused"
                self.db.execute("UPDATE paper_sessions SET status = 'paused', note = ? WHERE id = ?",
                                ("restored after restart; auto-resume is off", row["id"]))
            else:
                log_event(log, 20, f"paper session {row['id']} restored ({row['status']})")

    def _load(self, sid: str) -> None:
        row = self.db.query_one("SELECT * FROM paper_sessions WHERE id = ?", (sid,))
        if not row:
            raise NotFound(f"paper session {sid} not found")
        session = dict(row)
        for key in ("pairs", "config_snapshot", "confirm_timeframes", "breaker_state", "strategy_params"):
            session[key] = from_json(session.get(key), None)
        session["confirm_timeframes"] = session["confirm_timeframes"] or []
        settings = self.settings
        params = session.get("strategy_params") or {}
        if session["strategy_mode"] == "forward_test":
            names = [session["forward_strategy"]]
        else:
            names = list(STRATEGIES)
        self.strategies = {n: build(n, params.get(n) or {}) for n in names}
        costs = Costs.from_config(settings.costs)

        portfolio = Portfolio.new(float(session["initial_cash"]))
        portfolio.cash = float(session["cash"])
        trades = self.db.query("SELECT net_pnl FROM trades WHERE source = 'paper' AND run_id = ?", (sid,))
        realized = sum(float(t["net_pnl"]) for t in trades)
        for p in self.db.query("SELECT * FROM positions WHERE session_id = ? AND status = 'open'", (sid,)):
            pos = Position(
                pair=p["pair"], qty=p["qty"], entry_price=p["entry_price"], entry_ts=p["entry_ts"],
                entry_fee=p["entry_fee"], entry_slippage=p["entry_slippage"], stop=p["stop_price"],
                target=p["take_profit"], invalidation=p["invalidation"], initial_risk=p["initial_risk"],
                strategy=p["strategy"], strategy_version=p["strategy_version"], regime=p["regime"],
                environment=p["environment"], entry_reason=p["entry_reason"] or "", signal_ts=p["signal_ts"],
                time_stop_bars=p["time_stop_bars"], key=p["position_key"], bars_held=p["bars_held"],
                high_water=p["high_water"], low_water=p["low_water"], exit_qty=p["qty_exited"],
                exit_value=p["exit_value"], exit_fees=p["exit_fees"], exit_slippage=p["exit_slippage"], id=p["id"])
            portfolio.positions[pos.pair] = pos
            if pos.exit_qty > 0:
                realized += pos.exit_value - pos.exit_qty * pos.entry_price - pos.exit_fees \
                    - pos.entry_fee * (pos.exit_qty / pos.qty)
        portfolio.realized_pnl = realized
        fees = self.db.query_one("SELECT COALESCE(SUM(fee), 0) AS f, COALESCE(SUM(slippage_cost), 0) AS s FROM orders"
                                 " WHERE session_id = ?", (sid,))
        portfolio.fees_paid, portfolio.slippage_paid = float(fees["f"]), float(fees["s"])

        breakers = cb.BreakerState.from_dict(session.get("breaker_state") or
                                             cb.new_state(portfolio.cash, session["created_at"]).to_dict())
        recorder = PaperRecorder(self.db, sid, session["timeframe"], session["data_mode"], self.bus)

        def seen(order_id: str) -> bool:
            return self.db.scalar("SELECT 1 FROM orders WHERE client_order_id = ?", (order_id,)) is not None

        risk = RiskManager(settings.risk, costs, enforce_breakers=True, seen_order=seen)
        if session["strategy_mode"] == "forward_test":
            strat = self.strategies[session["forward_strategy"]]
            selector: Selector = FixedSelector(strat, label="forward test (not a validated selection)")
        else:
            if self.selector_factory is None:
                raise InvalidRequest("adaptive selection is unavailable")
            selector = self.selector_factory(session, self.strategies)
        core = TradingCore(
            run_id=sid, timeframe=session["timeframe"], confirm_timeframes=session["confirm_timeframes"], costs=costs,
            risk_cfg=settings.risk, risk=risk, selector=selector, strategies=self.strategies, portfolio=portfolio,
            breakers=breakers, recorder=recorder, health=self.engine_health,
            freshness=self._freshness, spread=self._spread, record_holds=True)
        for o in self.db.query("SELECT * FROM orders WHERE session_id = ? AND status IN ('pending', 'partially_filled')"
                               " AND NOT (purpose = 'entry' AND status = 'partially_filled')", (sid,)):
            core.pending[o["pair"]] = Order(
                client_order_id=o["client_order_id"], pair=o["pair"], side=o["side"], purpose=o["purpose"],
                reason=o["reason"] or "", created_ts=o["created_ts"], signal_ts=o["signal_ts"],
                qty=o["requested_qty"], ref_price=o["ref_price"] or 0.0, strategy=o["strategy"] or "",
                strategy_version=o["strategy_version"] or "", regime=o["regime"] or "", stop=o["stop_price"],
                target=o["take_profit"], invalidation=o["invalidation"], time_stop_bars=o["time_stop_bars"],
                environment=o["environment"], exit_reason=o["exit_reason"], status="pending", id=o["id"])
        last_marks = {}
        for pair in session["pairs"] or []:
            eq = self.market.store.load(session["exchange"], pair, session["timeframe"],
                                        end=self._cursor_for(sid, pair), limit=1, newest=True)
            if not eq.empty:
                last_marks[pair] = float(eq["close"].iloc[-1])
        core.marks.update(last_marks)
        self.session, self.core = session, core
        self._replay_states = {}

    def _cursor_for(self, sid: str, pair: str) -> int | None:
        v = self.db.scalar("SELECT last_processed_ts FROM paper_cursors WHERE session_id = ? AND pair = ?", (sid, pair))
        return int(v) if v is not None else None

    def _freshness(self, ps: PairState, i: int) -> tuple[bool, str]:
        s = self.session
        if s is None:
            return False, "no session"
        if s["data_mode"] == "replay":
            return True, "historical replay: the candle is processed at its own close (replay clock)"
        tf = s["timeframe"]
        expected = last_closed_open(self.clock(), tf)
        ts = int(ps.ts[i])
        if ts >= expected:
            return True, "latest closed candle"
        behind = (expected - ts) // tf_ms(tf)
        return False, (f"candle is {behind} candle(s) old (catching up after downtime or a feed delay); "
                       f"no new entries on old candles")

    def _spread(self, pair: str) -> tuple[float | None, str]:
        s = self.session
        if s and s["data_mode"] == "live":
            t = self.market.ticker(pair)
            if t and not t.get("stale") and t.get("spread_pct") is not None:
                return float(t["spread_pct"]), "live Binance quote"
        return None, "configured assumption"

    # ----- reporting ---------------------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        s = self.session
        kill = self.kill_switch()
        if s is None or self.core is None:
            return {"status": "idle", "session": None, "kill_switch": kill, "last_error": self.last_error}
        core = self.core
        snap = core.portfolio.snapshot(core.marks, core.costs)
        br = core.breakers
        return {
            "status": s["status"],
            "session": {k: s.get(k) for k in ("id", "created_at", "data_mode", "strategy_mode", "forward_strategy",
                                              "pairs", "timeframe", "exchange", "initial_cash", "clock_ts",
                                              "replay_start_ts", "replay_end_ts", "replay_speed", "confirm_timeframes",
                                              "started_at")},
            "label": ("HISTORICAL REPLAY - simulated trading on stored candles" if s["data_mode"] == "replay"
                      else "PAPER TRADING - simulated trading on live Binance public data"),
            "portfolio": snap,
            "positions": [p.to_dict() for p in core.portfolio.positions.values()],
            "pending_orders": [o.to_dict() for o in core.pending.values()],
            "breakers": {**br.to_dict(), "daily_loss_pct": cb.daily_loss_pct(br, snap["equity"]),
                         "drawdown_pct": cb.drawdown_pct(br, snap["equity"])},
            "kill_switch": kill,
            "engine": {"healthy": self.engine_health().healthy, "problems": list(self.engine_health().problems)},
            "pairs": self.pair_status,
            "stats": {"decisions": dict(core.stats.decisions), "rejections": dict(core.stats.rejections)},
            "last_error": self.last_error,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
        }

    def status_lite(self) -> dict[str, Any]:
        s = self.session
        return {"status": s["status"] if s else "idle", "kill_switch": bool(self.kill_switch().get("on")),
                "data_mode": s["data_mode"] if s else None}

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Test helper: wait until a replay has finished."""
        end = time.time() + timeout
        while time.time() < end:
            if not (self._thread and self._thread.is_alive()):
                return True
            time.sleep(0.02)
        return False
