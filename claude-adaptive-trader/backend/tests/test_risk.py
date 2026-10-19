"""The fourteen mandatory checks, the circuit breakers and the kill switch."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import CostConfig, RiskConfig, Settings
from app.execution.models import Costs
from app.risk import circuit_breakers as cb
from app.risk.risk_manager import (CHECKS, EngineHealth, MarketSnapshot, PortfolioSnapshot, RiskManager,
                                   TradeProposal)

DAY = 86_400_000
COSTS = Costs.from_config(CostConfig())
RISK = RiskConfig()


def proposal(**kw) -> TradeProposal:
    base = dict(run_id="r", pair="BTC/USDT", side="buy", strategy="trend_following", strategy_version="v",
                required_regimes=("trend_up",), regime="trend_up", regime_confidence=0.8, signal_ts=1_000,
                decision_ts=1_000 + 900_000, entry_ref=100.0, stop=98.0, target=104.0, invalidation=None,
                confidence=0.7, reason="test signal")
    base.update(kw)
    return TradeProposal(**base)


def market(**kw) -> MarketSnapshot:
    base = dict(pair="BTC/USDT", last_closed_open_ts=1_000, fresh=True, freshness_detail="fresh", complete=True,
                completeness_detail="complete", spread_pct=0.02, spread_source="test", avg_bar_volume=None, atr=1.0,
                amount_step=0.0001, min_qty=0.0001, min_notional=5.0, rules_source="test")
    base.update(kw)
    return MarketSnapshot(**base)


def portfolio(**kw) -> PortfolioSnapshot:
    base = dict(equity=10_000.0, cash=10_000.0, open_pairs=(), pending_pairs=(), open_risk=0.0, exposure=0.0,
                breakers=cb.new_state(10_000.0, 0))
    base.update(kw)
    return PortfolioSnapshot(**base)


def evaluate(p=None, m=None, pf=None, health=None, eligible=True, rm=None):
    rm = rm or RiskManager(RISK, COSTS)
    return rm.evaluate(p or proposal(), m or market(), pf or portfolio(), health or EngineHealth(True),
                       eligible=eligible, eligibility_detail="selector: eligible" if eligible else "not approved")


def test_valid_proposal_passes_all_fourteen() -> None:
    d = evaluate()
    assert d.approved, d.rejection
    assert [c.name for c in d.checks] == list(CHECKS) and len(CHECKS) == 14
    assert d.qty > 0 and d.sizing.risk_amount <= 10_000 * 0.0025 + 1e-6


@pytest.mark.parametrize("check, kwargs", [
    ("market_data_freshness", {"m": market(fresh=False, freshness_detail="stale by 3 candles")}),
    ("candle_completeness", {"m": market(complete=False, completeness_detail="gap")}),
    ("strategy_eligibility", {"eligible": False}),
    ("strategy_eligibility", {"p": proposal(regime="range")}),
    ("strategy_eligibility", {"p": proposal(regime="uncertain", required_regimes=("uncertain",))}),
    ("signal_validity", {"p": proposal(side="sell")}),
    ("signal_validity", {"p": proposal(signal_ts=999)}),
    ("signal_validity", {"p": proposal(reason="  ")}),
    ("stop_loss_validity", {"p": proposal(stop=101.0)}),
    ("stop_loss_validity", {"p": proposal(stop=99.95)}),
    ("stop_loss_validity", {"p": proposal(stop=80.0)}),
    ("position_size", {"pf": portfolio(open_risk=100.0)}),
    ("expected_transaction_costs", {"p": proposal(target=100.5)}),
    ("expected_transaction_costs", {"m": market(spread_pct=0.5)}),
    ("available_virtual_balance", {"pf": portfolio(cash=5.0)}),
    ("existing_positions", {"pf": portfolio(open_pairs=("BTC/USDT",))}),
    ("existing_positions", {"pf": portfolio(open_pairs=("A/USDT", "B/USDT", "C/USDT"))}),
    ("correlated_exposure", {"pf": portfolio(open_pairs=("ETH/USDT", "SOL/USDT"))}),
    ("correlated_exposure", {"pf": portfolio(exposure=6_100.0)}),
    ("daily_loss_limit", {"pf": portfolio(breakers=replace(cb.new_state(10_000.0, 0), daily_tripped=True))}),
    ("daily_loss_limit", {"pf": portfolio(equity=9_910.0, cash=9_910.0, breakers=cb.new_state(10_000.0, 0))}),
    ("drawdown_limit", {"pf": portfolio(breakers=replace(cb.new_state(10_000.0, 0), drawdown_tripped=True))}),
    ("drawdown_limit", {"pf": portfolio(equity=9_510.0, cash=9_510.0,
                                        breakers=replace(cb.new_state(9_510.0, 0), peak_equity=10_000.0))}),
    ("duplicate_order_prevention", {"rm": RiskManager(RISK, COSTS, seen_order=lambda oid: True)}),
    ("duplicate_order_prevention", {"pf": portfolio(pending_pairs=("BTC/USDT",))}),
    ("execution_engine_health", {"health": EngineHealth(False, ("kill switch is on",))}),
])
def test_each_check_can_reject_with_its_reason(check, kwargs) -> None:
    d = evaluate(**kwargs)
    assert not d.approved and d.qty == 0
    failed = [c.name for c in d.failed]
    assert check in failed, (check, failed)
    assert d.rejection.startswith(failed[0])
    assert len(d.checks) == 14                                  # every check still ran and was recorded


def test_size_limited_when_cash_is_short() -> None:
    d = evaluate(pf=portfolio(cash=1_000.0))
    assert d.approved and d.sizing.binding == "cash"
    assert d.qty * d.sizing.entry_estimate * (1 + COSTS.fee) <= 1_000.0


def test_research_runs_record_breaker_blocks_without_enforcing() -> None:
    rm = RiskManager(RISK, COSTS, enforce_breakers=False)
    st = replace(cb.new_state(10_000.0, 0), drawdown_tripped=True)
    d = evaluate(pf=portfolio(breakers=st), rm=rm)
    assert d.approved and any("drawdown_limit would have blocked" in n for n in d.notes)


def test_leverage_or_shorting_refused_at_construction() -> None:
    with pytest.raises(RuntimeError, match="leverage"):
        RiskManager(RiskConfig.model_construct(**{**RISK.model_dump(), "leverage_enabled": True}), COSTS)


def test_client_order_id_is_deterministic() -> None:
    assert proposal().client_order_id() == proposal().client_order_id()
    assert proposal().client_order_id() != proposal(signal_ts=2_000).client_order_id()


def test_daily_breaker_trips_and_resets_next_day() -> None:
    st = cb.new_state(10_000.0, 0)
    assert cb.update(st, 9_950.0, 3_600_000, RISK) == []
    events = cb.update(st, 9_899.0, 7_200_000, RISK)
    assert st.daily_tripped and events[0]["event"] == "daily_tripped"
    events = cb.update(st, 9_899.0, DAY + 60_000, RISK)
    assert not st.daily_tripped and st.day_start_equity == 9_899.0
    assert events[0]["event"] == "daily_reset"


def test_drawdown_breaker_latches_until_reset() -> None:
    st = cb.new_state(10_000.0, 0)
    cb.update(st, 10_500.0, 1, RISK)
    assert st.peak_equity == 10_500.0
    cb.update(st, 9_970.0, 2 * DAY, RISK)                    # 5.05% below the peak
    assert st.drawdown_tripped
    cb.update(st, 10_600.0, 3 * DAY, RISK)                   # recovery does not clear it
    assert st.drawdown_tripped and st.peak_equity == 10_500.0
    cb.reset_drawdown(st, 10_600.0, 4 * DAY, "user", "reviewed")
    assert not st.drawdown_tripped and st.peak_equity == 10_600.0


def test_settings_defaults_match_the_brief() -> None:
    r = Settings().risk
    assert (r.risk_per_trade_pct, r.max_risk_per_trade_pct, r.max_open_risk_pct, r.daily_loss_limit_pct,
            r.max_drawdown_pct) == (0.25, 0.5, 1.0, 1.0, 5.0)
