"""Event-driven backtester built on the same trading core as paper trading.

For each pair the candles are cut at the end of the period *before* anything is computed,
so no feature can see past it; features, regimes and signals are then computed once (they
are causal, which the no-look-ahead tests verify) and the core walks the candles in time
order, all pairs interleaved. Orders decided at a close execute at the next open, with the
conservative fill model, the fourteen risk checks and the circuit breakers.

Positions still open when the period ends are closed at the last close (with costs) and
marked as such, so every metric covers complete trades.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from app.config import Settings
from app.core.timeframes import tf_ms
from app.data.binance_client import assumed_rules
from app.execution.core import CoreStats, PairState, TradingCore
from app.execution.models import Bar, ClosedTrade, Costs
from app.execution.portfolio import Portfolio
from app.execution.recorders import MemoryRecorder
from app.risk import circuit_breakers as cb
from app.risk.risk_manager import RiskManager
from app.strategies.base import Strategy
from app.strategies.pipeline import context, prepare, signals
from app.strategies.registry import build
from app.strategies.selector import FixedSelector, Selector

END_OF_PERIOD = "closed at the end of the test period (not a strategy exit)"


@dataclass
class BacktestSpec:
    pairs: list[str]
    timeframe: str
    confirm_timeframes: list[str]
    exchange: str
    start: int
    end: int
    strategy: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    cost_multiplier: float = 1.0
    initial_cash: float = 10_000.0
    enforce_breakers: bool = False
    label: str = ""


@dataclass
class BacktestResult:
    spec: BacktestSpec
    trades: list[ClosedTrade]
    equity: list[dict[str, Any]]
    benchmark: list[float]
    stats: CoreStats
    decisions: list[dict[str, Any]]
    breaker_events: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    regimes: dict[str, dict[str, int]]
    notes: list[str]
    strategy_versions: dict[str, str]
    data_sources: dict[str, dict[str, int]]
    bars: int

    @property
    def final_equity(self) -> float:
        return float(self.equity[-1]["equity"]) if self.equity else self.spec.initial_cash


def prepare_views(candles: dict[str, pd.DataFrame], spec: BacktestSpec, settings: Settings) -> dict[str, PairState]:
    """Features and regimes per pair, computed once on candles up to ``spec.end``."""
    out = {}
    for pair in spec.pairs:
        cut = candles[pair][candles[pair]["ts"] <= spec.end].reset_index(drop=True)
        view = prepare(cut, pair, spec.timeframe, spec.confirm_timeframes, settings)
        out[pair] = PairState(pair, view, {}, {})
    return out


def run_backtest(spec: BacktestSpec, candles: dict[str, pd.DataFrame], settings: Settings, *,
                 strategies: dict[str, Strategy] | None = None, selector: Selector | None = None,
                 rules: Callable[[str], dict[str, Any]] | None = None, keep_decisions: bool = True,
                 precomputed: dict[str, PairState] | None = None) -> BacktestResult:
    step = tf_ms(spec.timeframe)
    if strategies is None:
        if not spec.strategy:
            raise ValueError("a backtest needs a strategy or a selector with strategies")
        strategies = {spec.strategy: build(spec.strategy, spec.params)}
    if selector is None:
        if len(strategies) != 1:
            raise ValueError("several strategies need a selector")
        selector = FixedSelector(next(iter(strategies.values())))
    rules = rules or (lambda pair: assumed_rules(pair).to_dict())
    costs = Costs.from_config(settings.costs, spec.cost_multiplier)
    ctx = context(settings, spec.timeframe, spec.confirm_timeframes, spec.cost_multiplier)

    states: dict[str, PairState] = {}
    sources: dict[str, dict[str, int]] = {}
    for pair in spec.pairs:
        df = candles[pair]
        if "source" in df:
            window = df[(df["ts"] >= spec.start) & (df["ts"] <= spec.end)]
            sources[pair] = {str(k): int(v) for k, v in window["source"].value_counts().items()}
        if precomputed and pair in precomputed:
            # Views computed on a longer history are identical row by row (every feature is
            # causal), and rows after spec.end are never visited by the loop below.
            view = precomputed[pair].view
        else:
            cut = df[df["ts"] <= spec.end].reset_index(drop=True)        # nothing after the period exists
            view = prepare(cut, pair, spec.timeframe, spec.confirm_timeframes, settings)
        sigs = {name: signals(view, strat, ctx) for name, strat in strategies.items()}
        states[pair] = PairState(pair, view, sigs, rules(pair))

    recorder = MemoryRecorder(keep_decisions=keep_decisions)
    portfolio = Portfolio.new(spec.initial_cash)
    breakers = cb.new_state(spec.initial_cash, spec.start)
    risk = RiskManager(settings.risk, costs, enforce_breakers=spec.enforce_breakers)
    core = TradingCore(run_id=spec.label or "backtest", timeframe=spec.timeframe,
                       confirm_timeframes=spec.confirm_timeframes, costs=costs, risk_cfg=settings.risk, risk=risk,
                       selector=selector, strategies=strategies, portfolio=portfolio, breakers=breakers,
                       recorder=recorder)

    timeline = sorted({int(t) for ps in states.values() for t in ps.ts if spec.start <= t <= spec.end})
    regimes: dict[str, dict[str, int]] = {}
    for pair, ps in states.items():
        mask = (ps.ts >= spec.start) & (ps.ts <= spec.end)
        regimes[pair] = {str(k): int(v) for k, v in ps.view.regime["regime"][mask].value_counts().items()}
    for ts in timeline:
        items = [(ps, ps.index[ts]) for ps in states.values() if ts in ps.index]
        core.step(items)

    notes: list[str] = []
    last_ts = timeline[-1] if timeline else spec.end
    for pair in list(core.pending):
        core.pending.pop(pair)
    open_pairs = list(portfolio.positions)
    for pair in open_pairs:
        ps = states[pair]
        i = ps.index.get(last_ts, len(ps.ts) - 1)
        price = float(ps.close[i])
        final_bar = Bar(pair, last_ts + step, price, price, price, price, float(ps.volume[i]), None)
        pos = portfolio.positions[pair]
        fill = core.sim.market("sell", final_bar, pos.remaining)
        if fill.rejected or fill.partial:
            # No volume to exit into: value it at the last close without slippage, and say so.
            fill.filled_qty = pos.remaining
            fill.price = price * (1 - costs.slippage - costs.half_spread)
            fill.fee = fill.filled_qty * fill.price * costs.fee
            fill.rejected = None
            fill.partial = False
        trade = portfolio.reduce_position(pair, fill, last_ts + step, END_OF_PERIOD)
        if trade:
            recorder.trade(trade)
    if open_pairs:
        notes.append(f"{len(open_pairs)} position(s) open at the end were closed at the last close with costs")
        core.mark(last_ts + step)
    portfolio.check_invariants(core.marks)
    if core.stats.breaker_would_block:
        notes.append(f"circuit breakers would have blocked {core.stats.breaker_would_block} entr"
                     f"{'y' if core.stats.breaker_would_block == 1 else 'ies'} (recorded, not enforced)")
    if core.stats.gap_cancellations:
        notes.append(f"{core.stats.gap_cancellations} entr{'y was' if core.stats.gap_cancellations == 1 else 'ies were'}"
                     f" cancelled because the execution candle was missing from the data")

    benchmark = buy_and_hold(states, [p["ts"] for p in recorder.equity_points], spec, costs)
    return BacktestResult(
        spec=spec, trades=recorder.trades, equity=recorder.equity_points, benchmark=benchmark, stats=core.stats,
        decisions=recorder.decisions, breaker_events=recorder.breakers, orders=recorder.orders, regimes=regimes,
        notes=notes, strategy_versions={n: s.version for n, s in strategies.items()}, data_sources=sources,
        bars=len(timeline))


def buy_and_hold(states: dict[str, PairState], times: list[int], spec: BacktestSpec, costs: Costs) -> list[float]:
    """Equal-weight buy and hold of the same pairs over the same period, with the same costs."""
    if not times:
        return []
    step = tf_ms(spec.timeframe)
    per_pair = spec.initial_cash / max(1, len(states))
    series = np.zeros(len(times))
    cash_left = 0.0
    for ps in states.values():
        mask = (ps.ts >= spec.start) & (ps.ts <= spec.end)
        idx = np.nonzero(mask)[0]
        if not len(idx):
            cash_left += per_pair
            continue
        first = idx[0]
        entry = ps.open[first] * (1 + costs.slippage + costs.half_spread)
        qty = per_pair / (entry * (1 + costs.fee))
        closes = pd.Series(ps.close, index=ps.ts + step)
        marks = closes.reindex(times, method="ffill").to_numpy()
        marks = np.where(np.isnan(marks), entry, marks)
        value = qty * marks
        exit_cost = qty * marks * (costs.slippage + costs.half_spread + costs.fee)
        value[-1] -= exit_cost[-1]
        series += value
    return (series + cash_left).tolist()
