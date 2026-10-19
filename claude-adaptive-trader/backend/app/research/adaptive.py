"""Evidence-based adaptive strategy selection.

The selector never looks at prices. It looks at *evidence*: out-of-sample results of each
strategy version, split by market regime, that were produced by the validation protocol
(walk-forward windows over development + validation data - never the frozen test set) or,
in a selector walk-forward, by backtests on the preceding training window only.

For the current regime of a pair, a strategy is *eligible* only if

* it is allowed to trade that regime,
* it is approved by a person (paper trading) - or under evaluation (research runs),
* its out-of-sample evidence in that regime has at least ``min_trades_for_eligibility``
  trades, a profit factor of at least ``min_profit_factor``, an expectancy above
  ``min_expectancy_r``, a drawdown within ``max_drawdown_pct`` and stays profitable at the
  stress cost multiple, and
* its recent paper results are not far below that evidence (a safeguard that only acts on
  a meaningful sample, so a few unlucky trades do not trigger it).

Eligible strategies are ranked by a conservative score: the lower confidence bound of
expectancy (mean minus one standard error) minus a complexity penalty, so a simpler strategy
wins over a more complex one with a similar but less certain edge. Switching is damped: a
challenger must lead the active strategy by ``switch_margin`` for ``hysteresis_evaluations``
consecutive evaluations. If nothing is eligible, the answer is "no trade".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

import numpy as np

from app.config import SelectorConfig
from app.strategies.base import Strategy
from app.strategies.selector import SelectorChoice
from app.storage.database import Database


@dataclass
class RegimeEvidence:
    trades: int
    expectancy_r: float | None
    profit_factor: float | None
    win_rate_pct: float | None
    lower_bound_r: float | None

    def to_dict(self) -> dict[str, Any]:
        return {"trades": self.trades, "expectancy_r": self.expectancy_r, "profit_factor": self.profit_factor,
                "win_rate_pct": self.win_rate_pct, "lower_bound_r": self.lower_bound_r}


@dataclass
class Evidence:
    strategy: str
    version: str
    status: str
    complexity: int
    required_regimes: tuple[str, ...]
    by_regime: dict[str, RegimeEvidence]
    max_drawdown_pct: float | None
    stress_expectancy_r: float | None
    recent_trades: int = 0
    recent_expectancy_r: float | None = None
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "version": self.version, "status": self.status,
                "complexity": self.complexity, "required_regimes": list(self.required_regimes),
                "by_regime": {k: v.to_dict() for k, v in self.by_regime.items()},
                "max_drawdown_pct": self.max_drawdown_pct, "stress_expectancy_r": self.stress_expectancy_r,
                "recent_trades": self.recent_trades, "recent_expectancy_r": self.recent_expectancy_r,
                "source": self.source}


def regime_evidence(r_multiples: Iterable[float], pnls: Iterable[float]) -> RegimeEvidence:
    r = np.array([x for x in r_multiples if x is not None and not math.isnan(x)], dtype=float)
    p = np.array(list(pnls), dtype=float)
    n = len(r)
    if n == 0:
        return RegimeEvidence(0, None, None, None, None)
    gains = p[p > 0].sum()
    losses = -p[p <= 0].sum()
    pf = float(gains / losses) if losses > 0 else None
    mean = float(r.mean())
    se = float(r.std(ddof=1) / math.sqrt(n)) if n > 1 else float("inf")
    return RegimeEvidence(n, round(mean, 4), round(pf, 4) if pf is not None else None,
                          round(float((p > 0).mean() * 100), 2), round(mean - se, 4) if math.isfinite(se) else None)


def evidence_from_trades(strategy: Strategy, status: str, trades: list[Mapping[str, Any]], *,
                         max_drawdown_pct: float | None, stress_expectancy_r: float | None,
                         source: str) -> Evidence:
    by: dict[str, list[Mapping[str, Any]]] = {}
    for t in trades:
        by.setdefault(str(t.get("regime") or "unknown"), []).append(t)
    return Evidence(
        strategy=strategy.name, version=strategy.version, status=status, complexity=strategy.complexity,
        required_regimes=strategy.required_regimes,
        by_regime={k: regime_evidence([float(t["r_multiple"]) if t.get("r_multiple") is not None else float("nan")
                                       for t in v], [float(t["net_pnl"]) for t in v]) for k, v in by.items()},
        max_drawdown_pct=max_drawdown_pct, stress_expectancy_r=stress_expectancy_r, source=source)


def eligibility(ev: Evidence, regime: str, cfg: SelectorConfig, *, require_approved: bool) -> tuple[bool, list[str]]:
    reasons = []
    if regime not in ev.required_regimes:
        reasons.append(f"does not trade in {regime}")
    if require_approved and ev.status != "approved":
        reasons.append(f"version is {ev.status}, not approved by a person")
    e = ev.by_regime.get(regime)
    if e is None or e.trades < cfg.min_trades_for_eligibility:
        reasons.append(f"{e.trades if e else 0} out-of-sample trades in {regime} "
                       f"(needs {cfg.min_trades_for_eligibility})")
    else:
        if e.profit_factor is None or e.profit_factor < cfg.min_profit_factor:
            reasons.append(f"profit factor {e.profit_factor} below {cfg.min_profit_factor}")
        if e.expectancy_r is None or e.expectancy_r <= cfg.min_expectancy_r:
            reasons.append(f"expectancy {e.expectancy_r} R not above {cfg.min_expectancy_r}")
    if ev.max_drawdown_pct is None or ev.max_drawdown_pct > cfg.max_drawdown_pct:
        reasons.append(f"out-of-sample drawdown {ev.max_drawdown_pct}% above {cfg.max_drawdown_pct}%")
    if ev.stress_expectancy_r is None or ev.stress_expectancy_r <= 0:
        reasons.append(f"not profitable at {cfg.stress_multiplier:g}x costs ({ev.stress_expectancy_r})")
    if ev.recent_trades >= cfg.recent_min_trades and ev.recent_expectancy_r is not None and e is not None \
            and e.lower_bound_r is not None and ev.recent_expectancy_r < min(e.lower_bound_r, 0.0) - 0.5:
        reasons.append(f"recent paper results ({ev.recent_expectancy_r} R over {ev.recent_trades} trades) are far "
                       f"below the validated evidence")
    return not reasons, reasons


def score(ev: Evidence, regime: str, cfg: SelectorConfig) -> float:
    e = ev.by_regime[regime]
    base = e.lower_bound_r if e.lower_bound_r is not None else (e.expectancy_r or 0.0)
    return float(base) - cfg.complexity_penalty * ev.complexity


@dataclass
class PairSelection:
    active: str | None = None
    challenger: str | None = None
    streak: int = 0


@dataclass
class AdaptiveSelector:
    evidence: dict[str, Evidence]
    cfg: SelectorConfig
    require_approved: bool = True
    load_state: Callable[[str], PairSelection | None] | None = None
    save_state: Callable[[str, PairSelection, int], None] | None = None
    states: dict[str, PairSelection] = field(default_factory=dict)

    def _state(self, pair: str) -> PairSelection:
        if pair not in self.states:
            loaded = self.load_state(pair) if self.load_state else None
            self.states[pair] = loaded or PairSelection()
        return self.states[pair]

    def table(self, regime: str) -> list[dict[str, Any]]:
        rows = []
        for name, ev in self.evidence.items():
            ok, reasons = eligibility(ev, regime, self.cfg, require_approved=self.require_approved)
            rows.append({"strategy": name, "version": ev.version, "eligible": ok, "reasons": reasons,
                         "score": round(score(ev, regime, self.cfg), 4) if ok else None})
        return sorted(rows, key=lambda r: (not r["eligible"], -(r["score"] or 0)))

    def choose(self, pair: str, ts: int, regime_row: Mapping[str, Any]) -> SelectorChoice:
        regime = str(regime_row["regime"])
        st = self._state(pair)
        if regime == "uncertain":
            changed = st.active is not None
            st.active, st.challenger, st.streak = None, None, 0
            if changed and self.save_state:
                self.save_state(pair, st, ts)
            return SelectorChoice(None, f"no trade: regime uncertain ({regime_row.get('regime_reason', '')})", False,
                                  "regime uncertain", {"regime": regime})
        table = self.table(regime)
        eligible = [r for r in table if r["eligible"]]
        scores = {"regime": regime, "candidates": table}
        if not eligible:
            changed = st.active is not None
            st.active, st.challenger, st.streak = None, None, 0
            if changed and self.save_state:
                self.save_state(pair, st, ts)
            why = "; ".join(f"{r['strategy']}: {r['reasons'][0]}" for r in table if r["reasons"]) or "no strategies"
            return SelectorChoice(None, f"no trade: no strategy is eligible in {regime} ({why})", False,
                                  "no eligible strategy", scores)
        best = eligible[0]
        by_name = {r["strategy"]: r for r in eligible}
        note = ""
        if st.active is None or st.active not in by_name:
            previous = st.active
            st.active, st.challenger, st.streak = best["strategy"], None, 0
            note = (f"selected {best['strategy']}" if previous is None else
                    f"switched from {previous} (no longer eligible in {regime}) to {best['strategy']}")
        elif best["strategy"] != st.active:
            lead = best["score"] - by_name[st.active]["score"]
            if lead >= self.cfg.switch_margin:
                st.streak = st.streak + 1 if st.challenger == best["strategy"] else 1
                st.challenger = best["strategy"]
                if st.streak >= self.cfg.hysteresis_evaluations:
                    note = (f"switched from {st.active} to {best['strategy']} after leading by "
                            f"{lead:.3f} for {st.streak} evaluations")
                    st.active, st.challenger, st.streak = best["strategy"], None, 0
                else:
                    note = (f"kept {st.active}; challenger {best['strategy']} leads by {lead:.3f} "
                            f"({st.streak}/{self.cfg.hysteresis_evaluations})")
            else:
                st.challenger, st.streak = None, 0
                note = f"kept {st.active}; {best['strategy']} is within the switch margin"
        else:
            st.challenger, st.streak = None, 0
            note = f"kept {st.active} (best eligible)"
        if self.save_state:
            self.save_state(pair, st, ts)
        scores.update({"active": st.active, "challenger": st.challenger, "streak": st.streak})
        return SelectorChoice(st.active, f"adaptive selector: {note}", True,
                              f"{st.active} eligible in {regime} on out-of-sample evidence", scores)


def evidence_from_db(db: Database, strategies: dict[str, Strategy], versions: dict[str, dict[str, Any]],
                     cfg: SelectorConfig) -> dict[str, Evidence]:
    """Evidence for the given versions from their latest validation experiment and recent paper trades."""
    out: dict[str, Evidence] = {}
    for name, strat in strategies.items():
        v = versions.get(name)
        if not v:
            continue
        eid = v.get("validation_experiment_id")
        trades = db.query("SELECT regime, r_multiple, net_pnl FROM trades WHERE source = 'backtest' AND run_id = ?"
                          " AND scenario = 'walk_forward_oos'", (eid,)) if eid else []
        validation = v.get("validation") or {}
        dd = stress = None
        for g in validation.get("gates") or []:
            if g.get("gate") == "max_drawdown":
                dd = g.get("value")
            if g.get("gate") == "cost_stress":
                stress = g.get("value")
        ev = evidence_from_trades(strat, v.get("status", "research"), trades, max_drawdown_pct=dd,
                                  stress_expectancy_r=stress, source=f"validation {eid}" if eid else "no validation")
        recent = db.query("SELECT r_multiple FROM trades WHERE source = 'paper' AND strategy_version = ?"
                          " ORDER BY exit_ts DESC LIMIT ?", (strat.version, cfg.recent_trades_window))
        r = [x["r_multiple"] for x in recent if x["r_multiple"] is not None]
        ev.recent_trades = len(r)
        ev.recent_expectancy_r = round(float(np.mean(r)), 4) if r else None
        out[name] = ev
    return out


def db_state_store(db: Database, session_id: str) -> tuple[Callable[[str], PairSelection | None],
                                                            Callable[[str, PairSelection, int], None]]:
    def load(pair: str) -> PairSelection | None:
        row = db.query_one("SELECT active_strategy, challenger, challenger_streak FROM selector_state"
                           " WHERE session_id = ? AND pair = ?", (session_id, pair))
        if not row:
            return None
        return PairSelection(row["active_strategy"], row["challenger"], int(row["challenger_streak"]))

    def save(pair: str, st: PairSelection, ts: int) -> None:
        db.execute("INSERT INTO selector_state (session_id, pair, active_strategy, challenger, challenger_streak,"
                   " updated_ts) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(session_id, pair) DO UPDATE SET"
                   " active_strategy = excluded.active_strategy, challenger = excluded.challenger,"
                   " challenger_streak = excluded.challenger_streak, updated_ts = excluded.updated_ts",
                   (session_id, pair, st.active, st.challenger, st.streak, ts))

    return load, save
