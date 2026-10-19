"""The centralised risk manager: fourteen mandatory checks before any simulated entry.

Every proposal is evaluated against all fourteen checks, in a fixed order, and every
result is recorded - including the ones after the first failure - so a rejection always
says exactly which rule stopped the trade and why. Nothing outside this module can open a
position, and nothing in this module depends on the optional AI research assistant.

 1. market_data_freshness       8. available_virtual_balance
 2. candle_completeness         9. existing_positions
 3. strategy_eligibility       10. correlated_exposure
 4. signal_validity            11. daily_loss_limit
 5. stop_loss_validity         12. drawdown_limit
 6. position_size              13. duplicate_order_prevention
 7. expected_transaction_costs 14. execution_engine_health

The size comes from ``position_sizing`` (the minimum of the risk-, cash-, liquidity-,
position- and exposure-constrained sizes, rounded to the exchange's step).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config import RiskConfig
from app.core import limits
from app.execution.models import Costs
from app.risk.circuit_breakers import BreakerState, daily_loss_pct, drawdown_pct
from app.risk.position_sizing import SizingResult, size_position

CHECKS = (
    "market_data_freshness", "candle_completeness", "strategy_eligibility", "signal_validity",
    "stop_loss_validity", "position_size", "expected_transaction_costs", "available_virtual_balance",
    "existing_positions", "correlated_exposure", "daily_loss_limit", "drawdown_limit",
    "duplicate_order_prevention", "execution_engine_health",
)


@dataclass(frozen=True)
class TradeProposal:
    run_id: str
    pair: str
    side: str
    strategy: str
    strategy_version: str
    required_regimes: tuple[str, ...]
    regime: str
    regime_confidence: float
    signal_ts: int              # open time of the decision candle
    decision_ts: int            # its close time: when the decision was taken
    entry_ref: float            # that candle's close
    stop: float
    target: float | None
    invalidation: float | None
    confidence: float
    reason: str
    time_stop_bars: int | None = None
    environment: str | None = None

    def client_order_id(self) -> str:
        raw = f"{self.run_id}|{self.pair}|{self.strategy}|{self.signal_ts}|{self.side}|entry"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class MarketSnapshot:
    pair: str
    last_closed_open_ts: int | None
    fresh: bool
    freshness_detail: str
    complete: bool
    completeness_detail: str
    spread_pct: float | None          # live spread if known, otherwise the configured assumption
    spread_source: str
    avg_bar_volume: float | None      # base units per candle
    atr: float | None
    amount_step: float | None
    min_qty: float | None
    min_notional: float | None
    rules_source: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    equity: float
    cash: float
    open_pairs: tuple[str, ...]
    pending_pairs: tuple[str, ...]
    open_risk: float
    exposure: float
    breakers: BreakerState


@dataclass(frozen=True)
class EngineHealth:
    healthy: bool
    problems: tuple[str, ...] = ()


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class RiskDecision:
    approved: bool
    qty: float
    client_order_id: str
    checks: list[CheckResult]
    sizing: SizingResult | None
    rejection: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {"approved": self.approved, "qty": self.qty, "client_order_id": self.client_order_id,
                "rejection": self.rejection, "checks": [c.to_dict() for c in self.checks],
                "sizing": self.sizing.to_dict() if self.sizing else None, "notes": self.notes}


class RiskManager:
    def __init__(self, cfg: RiskConfig, costs: Costs, *, enforce_breakers: bool = True,
                 seen_order: Callable[[str], bool] | None = None) -> None:
        if cfg.leverage_enabled or cfg.shorting_enabled or limits.LEVERAGE_ALLOWED or limits.SHORTING_ALLOWED:
            raise RuntimeError("leverage and short selling are not implemented")
        self.cfg = cfg
        self.costs = costs
        self.enforce_breakers = enforce_breakers
        self._seen_order = seen_order or (lambda _id: False)

    def evaluate(self, p: TradeProposal, m: MarketSnapshot, pf: PortfolioSnapshot, health: EngineHealth, *,
                 eligible: bool, eligibility_detail: str) -> RiskDecision:
        cfg, costs = self.cfg, self.costs
        checks: list[CheckResult] = []
        add = lambda name, ok, detail: checks.append(CheckResult(name, bool(ok), detail))  # noqa: E731
        order_id = p.client_order_id()

        # 1-2: data
        add("market_data_freshness", m.fresh, m.freshness_detail)
        add("candle_completeness", m.complete, m.completeness_detail)

        # 3: eligibility (selector/approval) and regime
        regime_ok = p.regime in p.required_regimes and p.regime != "uncertain"
        add("strategy_eligibility", eligible and regime_ok,
            eligibility_detail if not eligible else
            (f"regime {p.regime} is one of {list(p.required_regimes)}" if regime_ok else
             f"regime {p.regime} is not one of {list(p.required_regimes)}"))

        # 4: signal
        signal_problems = []
        if p.side != "buy":
            signal_problems.append("only long entries exist (spot, no short selling)")
        if m.last_closed_open_ts is None or p.signal_ts != m.last_closed_open_ts:
            signal_problems.append("signal is not from the latest closed candle")
        if not (math.isfinite(p.entry_ref) and p.entry_ref > 0):
            signal_problems.append("invalid reference price")
        if not (0 <= p.confidence <= 1):
            signal_problems.append("confidence outside 0..1")
        if not p.reason.strip():
            signal_problems.append("no recorded reason")
        add("signal_validity", not signal_problems, "; ".join(signal_problems) or
            f"long signal from the candle closed at {p.decision_ts}, confidence {p.confidence:.2f}")

        # 5: stop
        stop_problems = []
        dist_pct = (p.entry_ref - p.stop) / p.entry_ref * 100 if p.entry_ref > 0 else float("nan")
        if not (math.isfinite(p.stop) and 0 < p.stop < p.entry_ref):
            stop_problems.append("stop must be below the entry and above zero")
        elif dist_pct < cfg.min_stop_distance_pct:
            stop_problems.append(f"stop {dist_pct:.3f}% away is closer than {cfg.min_stop_distance_pct}% (noise)")
        elif dist_pct > cfg.max_stop_distance_pct:
            stop_problems.append(f"stop {dist_pct:.2f}% away is wider than {cfg.max_stop_distance_pct}%")
        if p.target is not None and not (p.target > p.entry_ref):
            stop_problems.append("take profit must be above the entry")
        add("stop_loss_validity", not stop_problems, "; ".join(stop_problems) or
            f"stop {dist_pct:.2f}% below the reference price")

        # 6: size
        sizing: SizingResult | None = None
        if not stop_problems:
            sizing = size_position(
                equity=pf.equity, cash=pf.cash, entry_price=p.entry_ref, stop_price=p.stop,
                risk_pct=cfg.risk_per_trade_pct, max_risk_pct=cfg.max_risk_per_trade_pct, open_risk=pf.open_risk,
                max_open_risk_pct=cfg.max_open_risk_pct, max_position_pct=cfg.max_position_pct,
                exposure=pf.exposure, max_total_exposure_pct=cfg.max_total_exposure_pct, costs=costs,
                avg_bar_volume=m.avg_bar_volume, liquidity_max_volume_pct=cfg.liquidity_max_volume_pct,
                amount_step=m.amount_step, min_qty=m.min_qty,
                min_notional=max(cfg.min_order_notional, m.min_notional or 0.0))
        qty = sizing.qty if sizing else 0.0
        if sizing is None:
            add("position_size", False, "not sized: the stop is invalid")
        elif sizing.rejected:
            add("position_size", False, sizing.rejected)
        else:
            add("position_size", True, f"{qty:g} units, {sizing.notional:.2f} notional, risking "
                                       f"{sizing.risk_amount:.2f} ({sizing.risk_amount / pf.equity * 100:.3f}% of "
                                       f"equity); binding constraint: {sizing.binding} (rules: {m.rules_source})")

        # 7: costs
        spread = m.spread_pct if m.spread_pct is not None else costs.half_spread * 200
        round_trip = costs.round_trip_pct
        reward_pct = ((p.target - p.entry_ref) / p.entry_ref * 100) if p.target else None
        basis = reward_pct if reward_pct is not None else (dist_pct if math.isfinite(dist_pct) else None)
        ratio = round_trip / basis if basis and basis > 0 else float("inf")
        cost_problems = []
        if spread > cfg.max_spread_pct:
            cost_problems.append(f"spread {spread:.4f}% ({m.spread_source}) above {cfg.max_spread_pct}%")
        if ratio > cfg.max_cost_to_reward:
            what = "expected reward" if reward_pct is not None else "risk"
            cost_problems.append(f"round-trip cost {round_trip:.3f}% is {ratio:.2f}x the {what} "
                                 f"(limit {cfg.max_cost_to_reward})")
        add("expected_transaction_costs", not cost_problems, "; ".join(cost_problems) or
            f"round-trip cost {round_trip:.3f}% = {ratio:.2f}x the "
            f"{'expected reward' if reward_pct is not None else 'risk'}; spread {spread:.4f}% ({m.spread_source})")

        # 8: cash
        need = qty * (sizing.entry_estimate if sizing else p.entry_ref) * (1 + costs.fee)
        add("available_virtual_balance", qty > 0 and need <= pf.cash + 1e-9,
            f"needs {need:.2f} of {pf.cash:.2f} available virtual cash" if qty > 0 else "nothing to pay for")

        # 9: existing positions (no pyramiding, no averaging down)
        pos_problems = []
        if p.pair in pf.open_pairs:
            pos_problems.append(f"a position in {p.pair} is already open (no adding, no averaging down)")
        if p.pair in pf.pending_pairs:
            pos_problems.append(f"an order for {p.pair} is already pending")
        if len(pf.open_pairs) >= cfg.max_positions:
            pos_problems.append(f"{len(pf.open_pairs)} positions open, the limit is {cfg.max_positions}")
        add("existing_positions", not pos_problems, "; ".join(pos_problems) or
            f"{len(pf.open_pairs)} of {cfg.max_positions} positions open")

        # 10: correlation and total exposure
        group = cfg.correlated_with(p.pair)
        correlated = [x for x in pf.open_pairs if x in group and x != p.pair]
        new_exposure = pf.exposure + (sizing.notional if sizing and not sizing.rejected else 0.0)
        exposure_pct = new_exposure / pf.equity * 100 if pf.equity else 0.0
        corr_problems = []
        if len(correlated) >= cfg.max_correlated_positions:
            corr_problems.append(f"{len(correlated)} correlated positions open ({', '.join(correlated)}); "
                                 f"limit {cfg.max_correlated_positions}")
        if exposure_pct > cfg.max_total_exposure_pct + 1e-9:
            corr_problems.append(f"total exposure would be {exposure_pct:.1f}% (limit {cfg.max_total_exposure_pct}%)")
        add("correlated_exposure", not corr_problems, "; ".join(corr_problems) or
            f"{len(correlated)} correlated position(s); exposure after entry {exposure_pct:.1f}%")

        # 11-12: breakers (with this trade's own risk counted)
        trade_risk_pct = (sizing.risk_amount / pf.equity * 100) if sizing and not sizing.rejected and pf.equity else 0.0
        st = pf.breakers
        day_loss = max(0.0, daily_loss_pct(st, pf.equity))
        dd = max(0.0, drawdown_pct(st, pf.equity))
        daily_ok = not st.daily_tripped and day_loss + trade_risk_pct <= cfg.daily_loss_limit_pct + 1e-9
        dd_ok = not st.drawdown_tripped and dd + trade_risk_pct <= cfg.max_drawdown_pct + 1e-9
        daily_detail = (f"daily loss {day_loss:.3f}% + this trade's risk {trade_risk_pct:.3f}% vs limit "
                        f"{cfg.daily_loss_limit_pct}%" + (" (breaker tripped)" if st.daily_tripped else ""))
        dd_detail = (f"drawdown {dd:.3f}% + this trade's risk {trade_risk_pct:.3f}% vs limit {cfg.max_drawdown_pct}%"
                     + (" (breaker tripped)" if st.drawdown_tripped else ""))
        notes = []
        if not self.enforce_breakers:
            if not daily_ok:
                notes.append("daily_loss_limit would have blocked this trade (research run: recorded, not enforced)")
            if not dd_ok:
                notes.append("drawdown_limit would have blocked this trade (research run: recorded, not enforced)")
            daily_ok = dd_ok = True
            daily_detail += " [recorded only]"
            dd_detail += " [recorded only]"
        add("daily_loss_limit", daily_ok, daily_detail)
        add("drawdown_limit", dd_ok, dd_detail)

        # 13: duplicates
        dup = self._seen_order(order_id)
        add("duplicate_order_prevention", not dup and p.pair not in pf.pending_pairs,
            f"order {order_id} was already submitted" if dup else
            (f"an order for {p.pair} is pending" if p.pair in pf.pending_pairs else f"order id {order_id} is new"))

        # 14: engine
        add("execution_engine_health", health.healthy,
            "; ".join(health.problems) if health.problems else "engine healthy, kill switch off")

        if [c.name for c in checks] != list(CHECKS):  # never skipped, even under python -O
            raise RuntimeError("the mandatory risk checks did not all run in order")
        failed = [c for c in checks if not c.passed]
        approved = not failed
        rejection = None if approved else f"{failed[0].name}: {failed[0].detail}"
        return RiskDecision(approved=approved, qty=qty if approved else 0.0, client_order_id=order_id,
                            checks=checks, sizing=sizing, rejection=rejection, notes=notes)
