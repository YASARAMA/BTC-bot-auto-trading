"""The trading core shared by the backtester and the paper trader.

One closed candle of one pair is processed in two phases:

* ``open_phase`` - what happens *during* the candle: an order decided at the previous close
  executes at this candle's open; then the open position's stop and target are checked
  against this candle's range (stop first when both are reached).
* ``close_phase`` - what is decided *at* the candle's close: exits for an open position
  (invalidation level, strategy exit, time stop) and, when flat, regime -> selector ->
  strategy signal -> the fourteen risk checks -> an entry order for the next open.

Between the two phases of a timestamp, equity is marked to the closes and the circuit
breakers are updated, so a decision always sees the latest equity. The backtester feeds
historical candles through ``step``; the paper trader feeds each newly closed live (or
replayed) candle through the very same method.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import pandas as pd

from app.config import RiskConfig
from app.core.timeframes import tf_ms
from app.execution.fill_simulator import FillSimulator
from app.execution.models import Bar, ClosedTrade, Costs, FillResult, Order, Position
from app.execution.portfolio import Portfolio
from app.risk import circuit_breakers
from app.risk.circuit_breakers import BreakerState
from app.risk.position_sizing import floor_step
from app.risk.risk_manager import (EngineHealth, MarketSnapshot, PortfolioSnapshot, RiskManager, TradeProposal)
from app.strategies.base import Strategy
from app.strategies.pipeline import MarketView
from app.strategies.selector import Selector


@dataclass
class PairState:
    pair: str
    view: MarketView
    signals: dict[str, pd.DataFrame]
    exchange_rules: dict[str, Any]

    def __post_init__(self) -> None:
        f = self.view.features
        self.ts = f["ts"].to_numpy(dtype="int64")
        self.open = f["open"].to_numpy(dtype="float64")
        self.high = f["high"].to_numpy(dtype="float64")
        self.low = f["low"].to_numpy(dtype="float64")
        self.close = f["close"].to_numpy(dtype="float64")
        self.volume = f["volume"].to_numpy(dtype="float64")
        self.atr = f["atr"].to_numpy(dtype="float64")
        self.avg_volume = f["volume"].shift(1).rolling(20, min_periods=5).mean().to_numpy(dtype="float64")
        self.index = {int(t): i for i, t in enumerate(self.ts)}
        # Plain arrays for the per-candle loop: pandas row access is far too slow there.
        r = self.view.regime
        self.regime = r["regime"].to_numpy(dtype=object)
        self.regime_conf = r["regime_confidence"].to_numpy(dtype="float64")
        self.regime_reason = r["regime_reason"].to_numpy(dtype=object)
        self.regime_dir = r["regime_direction"].to_numpy()
        self.sig = {name: {col: df[col].to_numpy() for col in df.columns} for name, df in self.signals.items()}

    def regime_row(self, i: int) -> dict[str, Any]:
        return {"regime": str(self.regime[i]), "regime_confidence": float(self.regime_conf[i]),
                "regime_reason": str(self.regime_reason[i]), "regime_direction": int(self.regime_dir[i])}

    def bar(self, i: int) -> Bar:
        prev_atr = self.atr[i - 1] if i > 0 and not math.isnan(self.atr[i - 1]) else None
        return Bar(self.pair, int(self.ts[i]), float(self.open[i]), float(self.high[i]), float(self.low[i]),
                   float(self.close[i]), float(self.volume[i]), prev_atr)


class Recorder(Protocol):
    def order(self, order: Order, *, event: str, fill: FillResult | None = None, note: str = "",
              ts: int | None = None) -> None: ...
    def position_opened(self, pos: Position) -> None: ...
    def position_changed(self, pos: Position) -> None: ...
    def trade(self, trade: ClosedTrade) -> None: ...
    def decision(self, row: dict[str, Any]) -> None: ...
    def equity(self, ts: int, snap: dict[str, Any]) -> None: ...
    def breaker(self, event: dict[str, Any]) -> None: ...


class NullRecorder:
    def order(self, order: Order, *, event: str, fill: FillResult | None = None, note: str = "",
              ts: int | None = None) -> None:
        pass

    def position_opened(self, pos: Position) -> None:
        pass

    def position_changed(self, pos: Position) -> None:
        pass

    def trade(self, trade: ClosedTrade) -> None:
        pass

    def decision(self, row: dict[str, Any]) -> None:
        pass

    def equity(self, ts: int, snap: dict[str, Any]) -> None:
        pass

    def breaker(self, event: dict[str, Any]) -> None:
        pass


@dataclass
class CoreStats:
    decisions: Counter = field(default_factory=Counter)
    rejections: Counter = field(default_factory=Counter)
    breaker_would_block: int = 0
    gap_cancellations: int = 0
    partial_fills: int = 0
    stop_gaps: int = 0


class TradingCore:
    def __init__(self, *, run_id: str, timeframe: str, confirm_timeframes: list[str], costs: Costs, risk_cfg: RiskConfig,
                 risk: RiskManager, selector: Selector, strategies: dict[str, Strategy], portfolio: Portfolio,
                 breakers: BreakerState, recorder: Recorder | None = None,
                 health: Callable[[], EngineHealth] | None = None,
                 freshness: Callable[[PairState, int], tuple[bool, str]] | None = None,
                 spread: Callable[[str], tuple[float | None, str]] | None = None,
                 record_holds: bool = False) -> None:
        self.run_id = run_id
        self.timeframe = timeframe
        self.step_ms = tf_ms(timeframe)
        self.confirm = confirm_timeframes
        self.costs = costs
        self.risk_cfg = risk_cfg
        self.risk = risk
        self.selector = selector
        self.strategies = strategies
        self.portfolio = portfolio
        self.breakers = breakers
        self.recorder: Recorder = recorder or NullRecorder()
        self.sim = FillSimulator(costs)
        self.health = health or (lambda: EngineHealth(True))
        self.freshness = freshness or (lambda ps, i: (True, "historical candle, processed at its close"))
        self.spread = spread or (lambda pair: (None, "configured assumption"))
        self.record_holds = record_holds
        self.pending: dict[str, Order] = {}
        self.marks: dict[str, float] = {}
        self.stats = CoreStats()

    # ----- the clock ---------------------------------------------------------------------------
    def step(self, items: list[tuple[PairState, int]]) -> None:
        """Process candles that closed at the same moment (one per pair)."""
        if not items:
            return
        for ps, i in items:
            self.open_phase(ps, i)
        close_ts = max(int(ps.ts[i]) for ps, i in items) + self.step_ms
        self.mark(close_ts)
        for ps, i in items:
            self.close_phase(ps, i)

    def mark(self, close_ts: int) -> dict[str, Any]:
        snap = self.portfolio.snapshot(self.marks, self.costs)
        events = circuit_breakers.update(self.breakers, snap["equity"], close_ts, self.risk_cfg)
        for e in events:
            self.recorder.breaker(e)
        snap["drawdown_pct"] = max(0.0, circuit_breakers.drawdown_pct(self.breakers, snap["equity"]))
        snap["daily_loss_pct"] = circuit_breakers.daily_loss_pct(self.breakers, snap["equity"])
        self.recorder.equity(close_ts, snap)
        return snap

    # ----- during the candle ---------------------------------------------------------------------
    def open_phase(self, ps: PairState, i: int) -> None:
        bar = ps.bar(i)
        order = self.pending.get(ps.pair)
        if order is not None:
            expected = order.signal_ts + self.step_ms
            if bar.ts != expected and order.purpose == "entry":
                self.pending.pop(ps.pair)
                order.status = "cancelled"
                self.stats.gap_cancellations += 1
                self.recorder.order(order, event="cancelled",
                                    note="no market data for the execution candle (data gap); entry cancelled")
            elif order.purpose == "entry":
                self._fill_entry(ps, order, bar)
            else:
                self._fill_exit(ps, order, bar)
        pos = self.portfolio.positions.get(ps.pair)
        if pos is not None:
            # Includes the entry candle itself: after the open fill, its own range can hit the stop.
            self._intrabar(ps, pos, bar)
        if ps.pair in self.portfolio.positions:
            pos = self.portfolio.positions[ps.pair]
            pos.high_water = max(pos.high_water, bar.high)
            pos.low_water = min(pos.low_water, bar.low)
            pos.bars_held += 1
            self.recorder.position_changed(pos)
        self.marks[ps.pair] = bar.close

    def _fill_entry(self, ps: PairState, order: Order, bar: Bar) -> None:
        self.pending.pop(ps.pair)
        fill = self.sim.market("buy", bar, order.qty)
        if fill.rejected:
            order.status = "rejected"
            self.recorder.order(order, event="rejected", note=fill.rejected)
            return
        need = fill.filled_qty * fill.price * (1 + self.costs.fee)
        if need > self.portfolio.cash + 1e-9:
            # The open gapped up from the decision price: buy what the cash allows, or nothing.
            affordable = floor_step(self.portfolio.cash / (fill.price * (1 + self.costs.fee)),
                                    ps.exchange_rules.get("amount_step"))
            min_notional = max(self.risk_cfg.min_order_notional, ps.exchange_rules.get("min_notional") or 0.0)
            if affordable * fill.price < min_notional:
                order.status = "rejected"
                self.recorder.order(order, event="rejected", note="insufficient virtual cash at the execution price")
                return
            fill = self.sim.market("buy", bar, affordable)
        stop_exit = float(order.stop or 0.0) * (1 - self.costs.slippage - self.costs.half_spread)
        initial_risk = fill.filled_qty * (fill.price - stop_exit * (1 - self.costs.fee)) + fill.fee
        pos = self.portfolio.open_position(order, fill, initial_risk=max(initial_risk, 1e-9),
                                           key=order.client_order_id)
        pos.entry_ts = bar.ts
        if fill.partial:
            self.stats.partial_fills += 1
        order.status = "partially_filled" if fill.partial else "filled"
        self.recorder.order(order, event=order.status, fill=fill, ts=bar.ts,
                            note="partial fill capped by candle volume; the rest was cancelled" if fill.partial else "")
        self.recorder.position_opened(pos)

    def _fill_exit(self, ps: PairState, order: Order, bar: Bar) -> None:
        pos = self.portfolio.positions.get(ps.pair)
        if pos is None:
            self.pending.pop(ps.pair)
            order.status = "cancelled"
            self.recorder.order(order, event="cancelled", note="no position left to exit")
            return
        fill = self.sim.market("sell", bar, pos.remaining)
        if fill.rejected:
            self.recorder.order(order, event="waiting", note=f"{fill.rejected}; retrying at the next candle")
            order.signal_ts = bar.ts       # try again at the next open
            return
        trade = self.portfolio.reduce_position(ps.pair, fill, bar.ts, order.exit_reason or order.reason)
        if trade is None:
            self.stats.partial_fills += 1
            order.status = "partially_filled"
            order.signal_ts = bar.ts
            self.recorder.order(order, event="partially_filled", fill=fill, ts=bar.ts,
                                note="volume cap reached; the remainder exits at the next open")
            return
        self.pending.pop(ps.pair)
        order.status = "filled"
        self.recorder.order(order, event="filled", fill=fill, ts=bar.ts)
        self._closed(trade)

    def _intrabar(self, ps: PairState, pos: Position, bar: Bar) -> None:
        hit = self.sim.stop_triggered(bar, pos.stop)
        if hit is not None:
            ref, gapped = hit
            fill = self.sim.market("sell", bar, pos.remaining, ref_price=ref, gapped=gapped)
            if fill.rejected:
                return
            if gapped:
                self.stats.stop_gaps += 1
            reason = "stop loss (gapped through: filled at the open)" if gapped else "stop loss"
            self._exit_fill(ps, pos, fill, bar, reason, "stop_loss")
            return
        if pos.target is not None and self.sim.target_filled(bar, pos.target):
            fill = self.sim.limit_sell(bar, pos.remaining, pos.target)
            if fill.filled_qty > 0:
                self._exit_fill(ps, pos, fill, bar, "take profit", "take_profit")

    def _exit_fill(self, ps: PairState, pos: Position, fill: FillResult, bar: Bar, reason: str, kind: str) -> None:
        order = Order(client_order_id=f"{pos.key}-{kind}-{bar.ts}", pair=ps.pair, side="sell", purpose="exit",
                      reason=reason, created_ts=bar.ts, signal_ts=bar.ts, qty=pos.remaining,
                      ref_price=fill.ref_price, strategy=pos.strategy, strategy_version=pos.strategy_version,
                      regime=pos.regime, exit_reason=reason, status="filled")
        trade = self.portfolio.reduce_position(ps.pair, fill, bar.ts, reason)
        if trade is None:
            self.stats.partial_fills += 1
            order.status = "partially_filled"
            self.recorder.order(order, event="partially_filled", fill=fill, ts=bar.ts,
                                note="volume cap reached; the remainder exits at the next open")
            if ps.pair not in self.pending:
                rest = Order(client_order_id=f"{pos.key}-{kind}-rest-{bar.ts}", pair=ps.pair, side="sell",
                             purpose="exit", reason=f"{reason} (remainder)", created_ts=bar.ts + self.step_ms,
                             signal_ts=bar.ts, qty=pos.remaining, ref_price=bar.close, strategy=pos.strategy,
                             strategy_version=pos.strategy_version, regime=pos.regime, exit_reason=reason)
                self.pending[ps.pair] = rest
                self.recorder.order(rest, event="created")
            return
        self.recorder.order(order, event="filled", fill=fill, ts=bar.ts)
        self._closed(trade)

    def _closed(self, trade: ClosedTrade) -> None:
        self.recorder.trade(trade)

    # ----- at the close ------------------------------------------------------------------------------
    def close_phase(self, ps: PairState, i: int) -> None:
        pos = self.portfolio.positions.get(ps.pair)
        if pos is not None:
            if ps.pair not in self.pending:
                self._consider_exit(ps, pos, i)
            return
        if ps.pair in self.pending:
            return
        self._consider_entry(ps, i)

    def _consider_exit(self, ps: PairState, pos: Position, i: int) -> None:
        close = float(ps.close[i])
        reason = None
        if pos.invalidation is not None and not math.isnan(pos.invalidation) and close < pos.invalidation:
            reason = f"invalidation: close {close:.6g} back below {pos.invalidation:.6g}"
        else:
            sig = ps.sig.get(pos.strategy)
            if sig is not None and bool(sig["exit_signal"][i]):
                reason = str(sig["exit_reason"][i]) or "strategy exit signal"
            elif pos.time_stop_bars and pos.bars_held >= pos.time_stop_bars:
                reason = f"time stop after {pos.bars_held} candles"
        if reason is None:
            return
        ts = int(ps.ts[i])
        order = Order(client_order_id=f"{pos.key}-exit-{ts}", pair=ps.pair, side="sell", purpose="exit",
                      reason=reason, created_ts=ts + self.step_ms, signal_ts=ts, qty=pos.remaining,
                      ref_price=close, strategy=pos.strategy, strategy_version=pos.strategy_version, regime=pos.regime,
                      exit_reason=reason)
        self.pending[ps.pair] = order
        self.recorder.order(order, event="created")
        self.stats.decisions["exit"] += 1
        self.recorder.decision({"ts": ts + self.step_ms, "signal_ts": ts, "pair": ps.pair, "action": "exit",
                                "accepted": True, "reason": reason, "strategy": pos.strategy,
                                "strategy_version": pos.strategy_version, "regime": str(ps.regime[i]),
                                "regime_confidence": float(ps.regime_conf[i])})

    def _environment(self, ps: PairState, i: int) -> str:
        if not self.confirm:
            return "unknown"
        col = f"h{self.confirm[-1]}_trend"
        v = ps.view.features[col].iloc[i] if col in ps.view.features else float("nan")
        if pd.isna(v):
            return f"{self.confirm[-1]} unknown"
        return f"{self.confirm[-1]} " + {1.0: "up", -1.0: "down", 0.0: "flat"}.get(float(v), "flat")

    def _consider_entry(self, ps: PairState, i: int) -> None:
        ts = int(ps.ts[i])
        reg = ps.regime_row(i)
        base = {"ts": ts + self.step_ms, "signal_ts": ts, "pair": ps.pair, "regime": reg["regime"],
                "regime_confidence": reg["regime_confidence"]}
        choice = self.selector.choose(ps.pair, ts, reg)
        if choice.strategy is None:
            self.stats.decisions["no_trade"] += 1
            if self.record_holds:
                self.recorder.decision({**base, "action": "no_trade", "accepted": False, "reason": choice.reason,
                                        "scores": choice.scores})
            return
        strat = self.strategies[choice.strategy]
        arrays = ps.sig[choice.strategy]
        if not bool(arrays["entry"][i]):
            self.stats.decisions["hold"] += 1
            if self.record_holds:
                self.recorder.decision({**base, "action": "hold", "accepted": False, "strategy": strat.name,
                                        "strategy_version": strat.version,
                                        "reason": f"{strat.name}: entry conditions not met", "scores": choice.scores})
            return
        sig = ps.signals[choice.strategy].iloc[i]
        close = float(ps.close[i])
        proposal = TradeProposal(
            run_id=self.run_id, pair=ps.pair, side="buy", strategy=strat.name, strategy_version=strat.version,
            required_regimes=strat.required_regimes, regime=str(reg["regime"]),
            regime_confidence=float(reg["regime_confidence"]), signal_ts=ts, decision_ts=ts + self.step_ms,
            entry_ref=close, stop=float(sig["stop"]),
            target=None if pd.isna(sig["target"]) else float(sig["target"]),
            invalidation=None if pd.isna(sig["invalidation"]) else float(sig["invalidation"]),
            confidence=float(sig["confidence"]), reason=strat.entry_reason(ps.view.features, ps.view.regime, i, sig),
            time_stop_bars=getattr(strat, "time_stop_bars", lambda: None)(), environment=self._environment(ps, i))
        fresh, fresh_detail = self.freshness(ps, i)
        complete, complete_detail = self._completeness(ps, i)
        spread_pct, spread_src = self.spread(ps.pair)
        market = MarketSnapshot(
            pair=ps.pair, last_closed_open_ts=ts, fresh=fresh, freshness_detail=fresh_detail, complete=complete,
            completeness_detail=complete_detail, spread_pct=spread_pct, spread_source=spread_src,
            avg_bar_volume=None if math.isnan(ps.avg_volume[i]) else float(ps.avg_volume[i]),
            atr=None if math.isnan(ps.atr[i]) else float(ps.atr[i]),
            amount_step=ps.exchange_rules.get("amount_step"), min_qty=ps.exchange_rules.get("min_qty"),
            min_notional=ps.exchange_rules.get("min_notional"),
            rules_source=str(ps.exchange_rules.get("source", "unknown")))
        snap = self.portfolio.snapshot(self.marks, self.costs)
        pf = PortfolioSnapshot(equity=snap["equity"], cash=snap["cash"],
                               open_pairs=tuple(self.portfolio.positions), pending_pairs=tuple(self.pending),
                               open_risk=snap["open_risk"], exposure=snap["exposure"], breakers=self.breakers)
        decision = self.risk.evaluate(proposal, market, pf, self.health(), eligible=choice.eligible,
                                      eligibility_detail=choice.eligibility_detail)
        if decision.notes:
            self.stats.breaker_would_block += 1
        row = {**base, "action": "enter", "accepted": decision.approved, "strategy": strat.name,
               "strategy_version": strat.version, "reason": proposal.reason if decision.approved else decision.rejection,
               "checks": [c.to_dict() for c in decision.checks], "scores": choice.scores,
               "signal": {"entry_ref": close, "stop": proposal.stop, "target": proposal.target,
                          "invalidation": proposal.invalidation, "confidence": proposal.confidence,
                          "qty": decision.qty, "sizing": decision.sizing.to_dict() if decision.sizing else None,
                          "notes": decision.notes, "environment": proposal.environment}}
        self.recorder.decision(row)
        if not decision.approved:
            self.stats.decisions["rejected"] += 1
            self.stats.rejections[decision.failed[0].name] += 1
            return
        self.stats.decisions["enter"] += 1
        order = Order(client_order_id=decision.client_order_id, pair=ps.pair, side="buy", purpose="entry",
                      reason=proposal.reason, created_ts=ts + self.step_ms, signal_ts=ts, qty=decision.qty,
                      ref_price=close, strategy=strat.name, strategy_version=strat.version, regime=proposal.regime,
                      stop=proposal.stop, target=proposal.target, invalidation=proposal.invalidation,
                      time_stop_bars=proposal.time_stop_bars, environment=proposal.environment)
        self.pending[ps.pair] = order
        self.recorder.order(order, event="created")

    def _completeness(self, ps: PairState, i: int, lookback: int = 50) -> tuple[bool, str]:
        start = max(0, i - lookback)
        window = ps.ts[start:i + 1]
        if len(window) > 1:
            gaps = int(((window[1:] - window[:-1]) != self.step_ms).sum())
            if gaps:
                return False, f"{gaps} gap(s) in the last {lookback} candles; indicators may be distorted"
        missing = [c for c in (f"h{tf}_trend" for tf in self.confirm)
                   if c in ps.view.features and pd.isna(ps.view.features[c].iloc[i])]
        if missing:
            return False, f"higher-timeframe data missing ({', '.join(missing)})"
        return True, f"decision candle closed; no gaps in the last {lookback} candles"

    # ----- manual actions --------------------------------------------------------------------------
    def cancel_pending_entries(self, note: str) -> int:
        n = 0
        for pair, order in list(self.pending.items()):
            if order.purpose == "entry":
                self.pending.pop(pair)
                order.status = "cancelled"
                self.recorder.order(order, event="cancelled", note=note)
                n += 1
        return n

    def close_all(self, ts: int, reason: str) -> int:
        n = 0
        for pair, pos in self.portfolio.positions.items():
            if pair in self.pending and self.pending[pair].purpose == "exit":
                continue
            if pair in self.pending:
                self.pending.pop(pair)
            order = Order(client_order_id=f"{pos.key}-manual-{ts}", pair=pair, side="sell", purpose="exit",
                          reason=reason, created_ts=ts, signal_ts=ts - self.step_ms, qty=pos.remaining,
                          ref_price=self.marks.get(pair, pos.entry_price), strategy=pos.strategy,
                          strategy_version=pos.strategy_version, regime=pos.regime, exit_reason=reason)
            self.pending[pair] = order
            self.recorder.order(order, event="created", note="manual close requested")
            n += 1
        return n
