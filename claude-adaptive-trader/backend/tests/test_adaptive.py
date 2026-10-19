"""Adaptive selection, the strategy lifecycle and the optional research assistant."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.core.errors import Conflict, InvalidRequest
from app.research.adaptive import (AdaptiveSelector, Evidence, PairSelection, RegimeEvidence, db_state_store,
                                   eligibility, evidence_from_trades, regime_evidence)
from app.research.claude_researcher import AssistantUnavailable, ClaudeResearcher, ExperimentProposal, ParamChange, ProposalSet
from app.research.experiment_tracker import ExperimentTracker
from app.storage.database import Database
from app.storage.schema import STATE_MIGRATIONS
from app.storage.settings_store import SettingsStore

CFG = Settings().selector


def ev(name: str, *, regimes=("trend_up",), trades=60, exp=0.3, lb=0.2, pf=1.6, dd=4.0, stress=0.1,
       status="approved", complexity=3) -> Evidence:
    return Evidence(strategy=name, version=f"{name}@1", status=status, complexity=complexity,
                    required_regimes=regimes,
                    by_regime={r: RegimeEvidence(trades, exp, pf, 55.0, lb) for r in regimes},
                    max_drawdown_pct=dd, stress_expectancy_r=stress)


def row(regime: str = "trend_up") -> dict:
    return {"regime": regime, "regime_confidence": 0.8, "regime_reason": "test", "regime_direction": 1}


@pytest.mark.parametrize("kwargs, reason", [
    ({"trades": 10}, "out-of-sample trades"),
    ({"pf": 1.1}, "profit factor"),
    ({"exp": -0.1}, "expectancy"),
    ({"dd": 25.0}, "drawdown"),
    ({"stress": -0.05}, "costs"),
    ({"status": "validated"}, "not approved"),
])
def test_eligibility_gates(kwargs, reason) -> None:
    ok, reasons = eligibility(ev("a", **kwargs), "trend_up", CFG, require_approved=True)
    assert not ok and any(reason in r for r in reasons)
    assert eligibility(ev("a"), "trend_up", CFG, require_approved=True)[0]
    assert not eligibility(ev("a"), "range", CFG, require_approved=True)[0]


def test_uncertain_or_nothing_eligible_means_no_trade() -> None:
    sel = AdaptiveSelector({"a": ev("a", trades=5)}, CFG)
    c = sel.choose("BTC/USDT", 1, row())
    assert c.strategy is None and "no strategy is eligible" in c.reason
    c = AdaptiveSelector({"a": ev("a")}, CFG).choose("BTC/USDT", 1, row("uncertain"))
    assert c.strategy is None and "uncertain" in c.reason


def test_conservative_score_prefers_certain_and_simple() -> None:
    sel = AdaptiveSelector({"risky": ev("risky", exp=0.6, lb=0.05), "steady": ev("steady", exp=0.3, lb=0.25)}, CFG)
    assert sel.choose("P", 1, row()).strategy == "steady"          # lower bound, not the raw mean
    simple = AdaptiveSelector({"complex": ev("complex", lb=0.2, complexity=12),
                               "simple": ev("simple", lb=0.2, complexity=2)}, CFG)
    assert simple.choose("P", 1, row()).strategy == "simple"


def test_hysteresis_requires_a_sustained_lead() -> None:
    evidence = {"a": ev("a", lb=0.20), "b": ev("b", lb=0.10)}
    sel = AdaptiveSelector(evidence, CFG)
    assert sel.choose("P", 1, row()).strategy == "a"
    evidence["b"].by_regime["trend_up"].lower_bound_r = 0.40          # b now clearly better
    picks = [sel.choose("P", t, row()).strategy for t in range(2, 2 + CFG.hysteresis_evaluations)]
    assert picks[:-1] == ["a"] * (CFG.hysteresis_evaluations - 1)
    assert picks[-1] == "b"
    evidence["a"].by_regime["trend_up"].lower_bound_r = 0.42          # a only slightly better: inside the margin
    assert sel.choose("P", 99, row()).strategy == "b"


def test_switches_immediately_when_the_active_strategy_becomes_ineligible() -> None:
    evidence = {"a": ev("a", lb=0.3), "b": ev("b", lb=0.1)}
    sel = AdaptiveSelector(evidence, CFG)
    assert sel.choose("P", 1, row()).strategy == "a"
    evidence["a"].stress_expectancy_r = -0.2
    c = sel.choose("P", 2, row())
    assert c.strategy == "b" and "no longer eligible" in c.reason


def test_recent_paper_collapse_suspends_a_strategy() -> None:
    e = ev("a", lb=0.2)
    e.recent_trades, e.recent_expectancy_r = 25, -1.2
    ok, reasons = eligibility(e, "trend_up", CFG, require_approved=True)
    assert not ok and "recent paper results" in reasons[-1]
    e.recent_trades = 3                                                 # too few to judge: no overreaction
    assert eligibility(e, "trend_up", CFG, require_approved=True)[0]


def test_selector_state_persists(tmp_path) -> None:
    db = Database(tmp_path / "s.sqlite", STATE_MIGRATIONS)
    load, save = db_state_store(db, "sess")
    save("BTC/USDT", PairSelection("a", "b", 2), 123)
    restored = db_state_store(db, "sess")[0]("BTC/USDT")
    assert restored == PairSelection("a", "b", 2)


def test_regime_evidence_math() -> None:
    e = regime_evidence([1.0, -1.0, 2.0, -0.5], [10.0, -10.0, 20.0, -5.0])
    assert e.trades == 4 and e.expectancy_r == pytest.approx(0.375) and e.profit_factor == 2.0
    assert e.lower_bound_r < e.expectancy_r
    from app.strategies.registry import build
    evd = evidence_from_trades(build("momentum_breakout"), "approved",
                               [{"regime": "trend_up", "r_multiple": 1.0, "net_pnl": 5.0}],
                               max_drawdown_pct=1.0, stress_expectancy_r=0.5, source="t")
    assert evd.by_regime["trend_up"].trades == 1


# ----- lifecycle -------------------------------------------------------------------------------------
@pytest.fixture
def tracker(tmp_path) -> ExperimentTracker:
    return ExperimentTracker(Database(tmp_path / "s.sqlite", STATE_MIGRATIONS))


def test_only_people_approve_and_only_validated_without_override(tracker) -> None:
    v = tracker.ensure_version("trend_following", {})
    with pytest.raises(InvalidRequest):
        tracker.approve(v["id"], actor="claude", note="looks good")
    with pytest.raises(Conflict):
        tracker.approve(v["id"], actor="user", note="looks good")
    approved = tracker.approve(v["id"], actor="user", note="forward test only", override=True)
    assert approved["status"] == "approved" and approved["approval_note"].startswith("OVERRIDE")
    v2 = tracker.ensure_version("trend_following", {"target_r": 2.5})
    tracker.db.execute("UPDATE strategy_versions SET status = 'validated' WHERE id = ?", (v2["id"],))
    tracker.approve(v2["id"], actor="user", note="passed the protocol")
    assert tracker.version(v["id"])["status"] == "retired"             # one approved version per strategy
    assert tracker.approved()["trend_following"]["id"] == v2["id"]


def test_same_parameters_same_version(tracker) -> None:
    a = tracker.ensure_version("mean_reversion", {"rsi_max": 38})
    b = tracker.ensure_version("mean_reversion", {"rsi_max": 38})
    assert a["id"] == b["id"]


def test_the_assistant_cannot_change_settings(tmp_path) -> None:
    db = Database(tmp_path / "s.sqlite", STATE_MIGRATIONS)
    store = SettingsStore(Settings(), db)
    with pytest.raises(InvalidRequest):
        store.update({"risk": {"risk_per_trade_pct": 0.5}}, actor="claude")


# ----- research assistant (fake client: no network) --------------------------------------------------------
class FakeMessages:
    def __init__(self, proposals: list[ExperimentProposal] | None = None, refuse: bool = False) -> None:
        self.calls: list[dict] = []
        self.proposals = proposals or []
        self.refuse = refuse

    def _resp(self, **extra):
        return SimpleNamespace(model="test-model", stop_reason="refusal" if self.refuse else "end_turn",
                               stop_details=SimpleNamespace(category="test", explanation=""),
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5), _request_id="req_1", **extra)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp(content=[SimpleNamespace(type="text", text="Too few trades to conclude anything.")])

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp(content=[], parsed_output=ProposalSet(proposals=self.proposals))


@pytest.fixture
def assistant(services):
    services.settings_store.update({"research": {"claude_enabled": True, "model": "test-model"}}, actor="test")
    fake = SimpleNamespace(messages=FakeMessages())
    r = ClaudeResearcher(lambda: services.settings, services.db, services.tracker, client_factory=lambda: fake)
    return r, fake, services


def completed_experiment(services, uses_test_set: bool = False) -> str:
    eid = services.tracker.create(kind="backtest", settings=services.settings, request={},
                                  strategies=["momentum_breakout@1.0+x"], pairs=["BTC/USDT"], timeframe="15m",
                                  segment="test" if uses_test_set else "development", uses_test_set=uses_test_set)
    services.tracker.finish(eid, summary={"trades": 3})
    return eid


def test_assistant_is_off_by_default(services) -> None:
    st = ClaudeResearcher(lambda: services.settings, services.db, services.tracker).status()
    assert not st["available"] and any("disabled" in p for p in st["problems"])
    with pytest.raises(AssistantUnavailable):
        ClaudeResearcher(lambda: services.settings, services.db, services.tracker).review_experiment("x")


def test_review_creates_a_note_and_never_sees_the_test_set(assistant) -> None:
    r, fake, svc = assistant
    note = r.review_experiment(completed_experiment(svc))
    assert note["source"] == "claude" and note["kind"] == "review" and "Too few trades" in note["body"]
    call = fake.messages.calls[0]
    assert call["model"] == "test-model" and call["thinking"] == {"type": "adaptive"} and "tools" not in call
    with pytest.raises(InvalidRequest):
        r.review_experiment(completed_experiment(svc, uses_test_set=True))


def test_refusals_are_recorded_not_raised(assistant) -> None:
    r, fake, svc = assistant
    fake.messages.refuse = True
    note = r.review_experiment(completed_experiment(svc))
    assert "declined" in note["body"]


def test_proposals_are_validated_and_need_a_person(assistant) -> None:
    r, fake, svc = assistant
    fake.messages.proposals = [
        ExperimentProposal(title="wider target", hypothesis="h", rationale="r", risk_of_overfitting="o",
                           param_changes=[ParamChange(name="target_r", value=3.0)]),
        ExperimentProposal(title="out of bounds", hypothesis="h", rationale="r", risk_of_overfitting="o",
                           param_changes=[ParamChange(name="target_r", value=-4.0)]),
        ExperimentProposal(title="touch risk", hypothesis="h", rationale="r", risk_of_overfitting="o",
                           param_changes=[ParamChange(name="risk_per_trade_pct", value=2.0)]),
    ]
    notes = r.propose_experiments("momentum_breakout", count=3)
    statuses = [n["status"] for n in notes]
    assert statuses == ["pending_approval", "rejected", "rejected"]
    assert "not a parameter" in notes[2]["payload"]["problems"][0]
    with pytest.raises(InvalidRequest):
        r.decide(notes[0]["id"], approve=True, actor="claude", note="self-approval")
    decided = r.decide(notes[0]["id"], approve=True, actor="user", note="worth a validation run")
    assert decided["status"] == "approved"
    assert decided["version"]["status"] == "research"                  # still has to pass validation
    assert decided["version"]["params"]["target_r"] == 3.0
    with pytest.raises(InvalidRequest):
        r.decide(notes[0]["id"], approve=True, actor="user", note="twice")
