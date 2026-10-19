"""Research workflows: single backtests, the validation protocol and the final test.

The validation protocol is fixed in advance and identical for every strategy version:

1. Register (or reuse) the dataset and its frozen chronological split.
2. Parameter sensitivity on the development segment.
3. Walk-forward over development + validation, at 1x and at the stress cost multiple.
4. The validation segment at every cost multiple (1x, 2x, 3x).
5. Bootstrap intervals, a random-entry baseline and buy & hold.
6. The preliminary acceptance gates on the walk-forward out-of-sample record.

The frozen test segment is not touched by any of this. A final test runs only on a validated
(or approved) version, only on an unchanged test set, and every run is logged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.backtesting import datasets
from app.backtesting.baselines import random_baseline
from app.backtesting.engine import BacktestResult, BacktestSpec, prepare_views, run_backtest
from app.backtesting.gates import evaluate_gates
from app.backtesting.metrics import compute_metrics, trades_frame
from app.backtesting.reports import group_table, markdown, metrics_table, write_csvs, write_markdown
from app.backtesting.sensitivity import sensitivity
from app.backtesting.stats import bootstrap
from app.backtesting.walk_forward import walk_forward
from app.config import Settings
from app.core.clock import iso
from app.core.errors import Conflict, InvalidRequest, TestSetLocked
from app.core.jobs import JobContext
from app.core.timeframes import tf_ms
from app.data.market_data import MarketDataService
from app.research.experiment_tracker import ExperimentTracker
from app.strategies.registry import STRATEGIES, build
from app.storage.database import Database


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    pairs: list[str] | None = None
    timeframe: str | None = None
    exchange: str = "binance"
    segment: Literal["development", "validation", "research"] = "development"
    cost_multipliers: list[float] | None = None
    initial_cash: float | None = Field(None, gt=0)
    enforce_breakers: bool = False
    random_baseline: bool = False
    label: str | None = Field(None, max_length=200)
    hypothesis: str | None = Field(None, max_length=2000)


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    pairs: list[str] | None = None
    timeframe: str | None = None
    exchange: str = "binance"
    label: str | None = Field(None, max_length=200)
    hypothesis: str | None = Field(None, max_length=2000)


class SelectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pairs: list[str] | None = None
    timeframe: str | None = None
    exchange: str = "binance"
    label: str | None = Field(None, max_length=200)


class FinalTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: int
    reason: str = Field(..., min_length=10, max_length=1000)
    pairs: list[str] | None = None
    timeframe: str | None = None
    exchange: str = "binance"


Progress = Callable[[float, str], None]


def _noop(_f: float, _m: str) -> None:
    pass


class ResearchRunner:
    def __init__(self, settings: Callable[[], Settings], db: Database, market: MarketDataService,
                 tracker: ExperimentTracker) -> None:
        self._settings = settings
        self.db = db
        self.market = market
        self.tracker = tracker

    @property
    def settings(self) -> Settings:
        return self._settings()

    # ----- shared plumbing ------------------------------------------------------------------------
    def _scope(self, pairs: list[str] | None, timeframe: str | None, exchange: str) -> tuple[list[str], str, list[str]]:
        s = self.settings
        tf = timeframe or s.exchange.signal_timeframe
        pairs = sorted(pairs or s.exchange.pairs)
        if tf == s.exchange.signal_timeframe:
            confirm = list(s.exchange.confirm_timeframes)
        else:
            base = tf_ms(tf)
            confirm = [x for x in ("1h", "4h", "1d") if tf_ms(x) > base and tf_ms(x) % base == 0][:2]
        return pairs, tf, confirm

    def _rules(self, exchange: str) -> Callable[[str], dict[str, Any]]:
        """Exchange rules resolved once per experiment (one lookup per pair, then reused)."""
        from app.data.binance_client import assumed_rules

        cache: dict[str, dict[str, Any]] = {}

        def lookup(pair: str) -> dict[str, Any]:
            if pair not in cache:
                cache[pair] = (self.market.rules(pair) if exchange == "binance" else assumed_rules(pair)).to_dict()
            return cache[pair]

        return lookup

    def _report_dir(self, eid: str) -> Path:
        return self.settings.reports_path / eid

    def _spec(self, strategy: str, params: dict[str, Any], pairs: list[str], tf: str, confirm: list[str],
              exchange: str, window: tuple[int, int], mult: float, cash: float, enforce: bool, label: str) -> BacktestSpec:
        return BacktestSpec(pairs=pairs, timeframe=tf, confirm_timeframes=confirm, exchange=exchange, start=window[0],
                            end=window[1], strategy=strategy, params=params, cost_multiplier=mult, initial_cash=cash,
                            enforce_breakers=enforce, label=label)

    # ----- a single backtest -------------------------------------------------------------------------
    def backtest(self, req: BacktestRequest, ctx: JobContext | None = None, created_by: str = "user") -> dict[str, Any]:
        if req.strategy not in STRATEGIES:
            raise InvalidRequest(f"unknown strategy {req.strategy}")
        strat = build(req.strategy, req.params)
        s = self.settings
        progress: Progress = ctx.progress if ctx else _noop
        pairs, tf, confirm = self._scope(req.pairs, req.timeframe, req.exchange)
        ds = datasets.register(self.db, self.market, exchange=req.exchange, pairs=pairs, timeframe=tf,
                               split=s.backtest.split)
        window = ds.window(req.segment)
        candles = datasets.load_candles(self.market, req.exchange, pairs, tf, end=ds.validation_end_ts)
        version = self.tracker.ensure_version(req.strategy, strat.params_dict, created_by=created_by)
        mults = sorted(set(req.cost_multipliers or s.costs.stress_multipliers))
        cash = req.initial_cash or s.portfolio.initial_cash
        eid = self.tracker.create(kind="backtest", settings=s, request=req.model_dump(mode="json"),
                                  strategies=[strat.version], pairs=pairs, timeframe=tf, segment=req.segment,
                                  created_by=created_by, label=req.label or f"{req.strategy} on {req.segment}",
                                  hypothesis=req.hypothesis or strat.hypothesis, dataset_id=ds.id, start_ts=window[0],
                                  end_ts=window[1], seed=s.backtest.seed)
        try:
            rules = self._rules(req.exchange)
            spec0 = self._spec(req.strategy, strat.params_dict, pairs, tf, confirm, req.exchange, window, 1.0, cash,
                               req.enforce_breakers, eid)
            views = prepare_views(candles, spec0, s)
            scenarios: dict[str, dict[str, Any]] = {}
            base_res: BacktestResult | None = None
            for j, mult in enumerate(mults):
                progress(j / (len(mults) + 1), f"backtest at {mult:g}x costs")
                spec = self._spec(req.strategy, strat.params_dict, pairs, tf, confirm, req.exchange, window, mult,
                                  cash, req.enforce_breakers, eid)
                res = run_backtest(spec, candles, s, precomputed=views, rules=rules)
                name = "base" if mult == 1.0 else f"cost_{mult:g}x"
                m = compute_metrics(res.trades, res.equity, initial_cash=cash, benchmark=res.benchmark)
                scenarios[name] = {"metrics": m, "multiplier": mult, "stats": dict(res.stats.rejections),
                                   "notes": res.notes}
                self.tracker.store_trades(eid, name, req.segment, tf, res.trades)
                self.tracker.store_equity(eid, name, res.equity, res.benchmark)
                if mult == 1.0 or base_res is None:
                    base_res = res
            assert base_res is not None
            base_key = "base" if "base" in scenarios else next(iter(scenarios))
            boot = bootstrap(trades_frame(base_res.trades), samples=s.backtest.bootstrap_samples, seed=s.backtest.seed)
            baseline = None
            if req.random_baseline:
                progress(len(mults) / (len(mults) + 1), "random-entry baseline")
                bm = scenarios[base_key]["metrics"]
                baseline = random_baseline(candles, s, spec0, runs=s.backtest.random_baseline_runs,
                                           strategy_trades=bm["trades"], strategy_expectancy_r=bm["expectancy_r"],
                                           rules=rules, seed=s.backtest.seed)
            summary = self._summary(scenarios, base_key, boot, baseline, base_res)
            summary["version_id"] = version["id"]
            self._write_report(eid, base_res, scenarios, base_key, boot, baseline, extra={})
            self.tracker.finish(eid, metrics={k: v["metrics"] for k, v in scenarios.items()}, summary=summary,
                                report_dir=str(self._report_dir(eid)), data_sources=base_res.data_sources)
            progress(1.0, "done")
            return {"experiment_id": eid, **summary}
        except Exception as exc:
            self.tracker.fail(eid, f"{type(exc).__name__}: {exc}")
            raise

    def _summary(self, scenarios: dict[str, Any], base_key: str, boot: dict[str, Any], baseline: dict[str, Any] | None,
                 res: BacktestResult) -> dict[str, Any]:
        m = scenarios[base_key]["metrics"]
        return {
            "trades": m["trades"], "net_return_pct": m["net_return_pct"], "expectancy_r": m["expectancy_r"],
            "profit_factor": m["profit_factor"], "max_drawdown_pct": m["max_drawdown_pct"],
            "win_rate_pct": m["win_rate_pct"], "sharpe": m["sharpe"],
            "benchmark_return_pct": m["benchmark_return_pct"],
            "stress": {k: {"expectancy_r": v["metrics"]["expectancy_r"], "net_return_pct": v["metrics"]["net_return_pct"],
                           "trades": v["metrics"]["trades"]} for k, v in scenarios.items()},
            "bootstrap": {"expectancy_r_ci": boot["expectancy_r"]["ci"] if boot.get("available") else None},
            "random_baseline": {"beats_pct": baseline.get("strategy_beats_random_pct")} if baseline else None,
            "regimes": res.regimes, "notes": res.notes,
        }

    def _write_report(self, eid: str, res: BacktestResult, scenarios: dict[str, Any], base_key: str,
                      boot: dict[str, Any], baseline: dict[str, Any] | None, extra: dict[str, Any],
                      summary_title: str = "Summary") -> None:
        directory = self._report_dir(eid)
        write_csvs(directory, base_key, res.trades, res.equity, res.benchmark)
        exp = self.tracker.get(eid)
        exp["data_sources"] = res.data_sources
        exp["status"] = "completed"
        m = scenarios[base_key]["metrics"]
        sections: dict[str, Any] = {}
        sections[summary_title] = metrics_table(m)
        stress_rows = ["| Cost multiple | Trades | Net return | Expectancy (R) | Profit factor |", "|---|---:|---:|---:|---:|"]
        for name, sc in scenarios.items():
            sm = sc["metrics"]
            stress_rows.append(f"| {sc['multiplier']:g}x | {sm['trades']} | {sm['net_return_pct']}% | {sm['expectancy_r']} |"
                               f" {sm['profit_factor']} |")
        sections["Cost stress"] = stress_rows
        sections["Performance by market regime"] = group_table("By regime at entry", m["by_regime"])
        sections["Performance by environment"] = group_table("By slowest confirmation timeframe trend",
                                                             m["by_environment"])
        sections["Exits"] = group_table("By exit reason", m["by_exit_reason"])
        months = m.get("monthly_returns_pct") or {}
        sections["Monthly returns"] = (["| Month | Return |", "|---|---:|"] +
                                       [f"| {k} | {v}% |" for k, v in months.items()]) if months else "No data."
        if boot.get("available"):
            ci = boot["expectancy_r"]["ci"]
            sections["Uncertainty"] = [
                f"95% bootstrap interval of expectancy: {ci[0]:.3f} R to {ci[1]:.3f} R "
                f"({boot['samples']} resamples, seed {boot['seed']}).",
                f"Probability that expectancy is below zero (bootstrap): {boot['prob_expectancy_below_zero']:.1%}.",
                f"Caveat: {boot['caveat']}."]
        else:
            sections["Uncertainty"] = boot.get("reason", "not available")
        if baseline and baseline.get("available"):
            sections["Random-entry baseline"] = [
                f"{baseline['runs']} runs with random entry timing in the same regimes and the same stop/target "
                f"structure. Median random expectancy: {baseline['random_expectancy_r_median']} R. "
                f"The strategy beat {baseline['strategy_beats_random_pct']}% of them."]
        for title, body in extra.items():
            sections[title] = body
        rejections = scenarios[base_key].get("stats") or {}
        if rejections:
            sections["Rejected signals"] = ["| Check that failed first | Count |", "|---|---:|"] + [
                f"| {k} | {v} |" for k, v in sorted(rejections.items(), key=lambda kv: -kv[1])]
        if res.notes or m.get("notes"):
            sections["Notes"] = [f"- {n}" for n in [*res.notes, *m.get("notes", [])]]
        write_markdown(directory, markdown(exp, sections), {k: v["metrics"] for k, v in scenarios.items()})

    # ----- the validation protocol ----------------------------------------------------------------------
    def validate(self, req: ValidationRequest, ctx: JobContext | None = None, created_by: str = "user") -> dict[str, Any]:
        if req.strategy not in STRATEGIES:
            raise InvalidRequest(f"unknown strategy {req.strategy}")
        strat = build(req.strategy, req.params)
        s = self.settings
        progress: Progress = ctx.progress if ctx else _noop
        pairs, tf, confirm = self._scope(req.pairs, req.timeframe, req.exchange)
        ds = datasets.register(self.db, self.market, exchange=req.exchange, pairs=pairs, timeframe=tf,
                               split=s.backtest.split)
        candles = datasets.load_candles(self.market, req.exchange, pairs, tf, end=ds.validation_end_ts)
        version = self.tracker.ensure_version(req.strategy, strat.params_dict, created_by=created_by,
                                              hypothesis=req.hypothesis)
        research = ds.window("research")
        eid = self.tracker.create(kind="validation", settings=s, request=req.model_dump(mode="json"),
                                  strategies=[strat.version], pairs=pairs, timeframe=tf, segment="research",
                                  created_by=created_by, label=req.label or f"validation of {strat.version}",
                                  hypothesis=req.hypothesis or strat.hypothesis, dataset_id=ds.id,
                                  start_ts=research[0], end_ts=research[1], seed=s.backtest.seed)
        try:
            rules = self._rules(req.exchange)
            cash = s.portfolio.initial_cash
            params = strat.params_dict
            dev = ds.window("development")
            progress(0.02, "parameter sensitivity on the development segment")
            sens = sensitivity(candles, s, self._spec(req.strategy, params, pairs, tf, confirm, req.exchange, dev, 1.0,
                                                      cash, False, f"{eid}-dev"), rules=rules,
                               progress=lambda f, m: progress(0.02 + 0.3 * f, m))
            progress(0.33, "walk-forward (out-of-sample windows)")
            wf_spec = self._spec(req.strategy, params, pairs, tf, confirm, req.exchange, research, 1.0, cash, False,
                                 f"{eid}-wf")
            wf = walk_forward(candles, s, wf_spec, k=s.backtest.walk_forward_windows, train_ratio=s.backtest.train_ratio,
                              min_trades=s.backtest.min_trades_per_window, rules=rules,
                              progress=lambda f, m: progress(0.33 + 0.2 * f, m))
            stress_mult = s.gates.stress_multiplier
            wf_stress = walk_forward(candles, s, BacktestSpec(**{**wf_spec.__dict__, "cost_multiplier": stress_mult}),
                                     k=s.backtest.walk_forward_windows, train_ratio=s.backtest.train_ratio,
                                     min_trades=s.backtest.min_trades_per_window, rules=rules,
                                     progress=lambda f, m: progress(0.53 + 0.15 * f, f"{m} at {stress_mult:g}x costs"))
            val = ds.window("validation")
            views = prepare_views(candles, self._spec(req.strategy, params, pairs, tf, confirm, req.exchange, val, 1.0,
                                                      cash, False, eid), s)
            scenarios: dict[str, Any] = {}
            base_res: BacktestResult | None = None
            for mult in sorted(set(s.costs.stress_multipliers)):
                progress(0.7, f"validation segment at {mult:g}x costs")
                spec = self._spec(req.strategy, params, pairs, tf, confirm, req.exchange, val, mult, cash, False, eid)
                res = run_backtest(spec, candles, s, precomputed=views, rules=rules)
                name = "base" if mult == 1.0 else f"cost_{mult:g}x"
                scenarios[name] = {"metrics": compute_metrics(res.trades, res.equity, initial_cash=cash,
                                                              benchmark=res.benchmark), "multiplier": mult,
                                   "stats": dict(res.stats.rejections), "notes": res.notes}
                self.tracker.store_trades(eid, f"validation_{name}", "validation", tf, res.trades)
                self.tracker.store_equity(eid, f"validation_{name}", res.equity, res.benchmark)
                if mult == 1.0 or base_res is None:
                    base_res = res
            assert base_res is not None
            base_key = "base" if "base" in scenarios else next(iter(scenarios))
            self.tracker.store_trades(eid, "walk_forward_oos", "research", tf, wf.oos_trades)
            self.tracker.store_equity(eid, "walk_forward_oos", wf.oos_equity, None)
            oos_df = trades_frame(wf.oos_trades)
            boot = bootstrap(oos_df, samples=s.backtest.bootstrap_samples, seed=s.backtest.seed)
            progress(0.85, "random-entry baseline")
            vm = scenarios[base_key]["metrics"]
            baseline = random_baseline(candles, s, self._spec(req.strategy, params, pairs, tf, confirm, req.exchange,
                                                              val, 1.0, cash, False, eid),
                                       runs=s.backtest.random_baseline_runs, strategy_trades=vm["trades"],
                                       strategy_expectancy_r=vm["expectancy_r"], rules=rules, seed=s.backtest.seed)
            gates = evaluate_gates(oos=wf.oos_metrics, stress=wf_stress.oos_metrics, stability=sens["stability"],
                                   window_ratio=wf.profitable_window_ratio, bootstrap=boot, cfg=s.gates, risk=s.risk)
            updated = self.tracker.record_validation(version["id"], gates, eid)
            summary = self._summary(scenarios, base_key, boot, baseline, base_res)
            summary.update({
                "version_id": version["id"], "version_status": updated["status"], "verdict": gates["verdict"],
                "verdict_message": gates["message"], "failed_gates": gates["failed"],
                "walk_forward": {"oos": {k: wf.oos_metrics.get(k) for k in ("trades", "net_return_pct", "expectancy_r",
                                                                             "profit_factor", "max_drawdown_pct")},
                                 "windows": [{k: w[k] for k in ("window", "test", "trades", "net_return_pct",
                                                                "expectancy_r", "profit_factor")} for w in wf.windows],
                                 "profitable_window_ratio": wf.profitable_window_ratio,
                                 "stress_oos_expectancy_r": wf_stress.oos_metrics.get("expectancy_r")},
                "sensitivity": {"stability": sens["stability"], "neighbours": sens["neighbours"]},
            })
            extra = {
                "Validation protocol": [
                    "1. Frozen chronological split; the test segment was not used.",
                    "2. Parameter sensitivity on the development segment.",
                    f"3. Walk-forward with {len(wf.windows)} rolling windows over development + validation, at 1x and "
                    f"{stress_mult:g}x costs.",
                    "4. Validation segment at every cost multiple.",
                    "5. Bootstrap intervals, random-entry baseline, buy & hold.",
                    "6. Preliminary acceptance gates on the walk-forward out-of-sample record."],
                "Walk-forward (out of sample)": metrics_table(wf.oos_metrics) + [""] + [
                    "| Window | Test period | Trades | Net return | Expectancy (R) |", "|---|---|---:|---:|---:|"] + [
                    f"| {w['window']} | {iso(w['test'][0])} to {iso(w['test'][1])} | {w['trades']} | "
                    f"{w['net_return_pct']}% | {w['expectancy_r']} |" for w in wf.windows],
                "Parameter sensitivity (development)": [
                    f"Stability: {sens['stability']} ({sens['note']}).", "",
                    "| Parameter | Value (base) | Trades | Expectancy (R) |", "|---|---|---:|---:|"] + [
                    f"| {r['param']} | {r['value']} ({r['base_value']}) | {r['trades']} | {r['expectancy_r']} |"
                    for r in sens["neighbours"]],
                "Acceptance gates": ["| Gate | Passed | Value | Threshold |", "|---|---|---|---|"] + [
                    f"| {g['gate']} | {'yes' if g['passed'] else 'NO'} | {g['value']} | {g['threshold']} |"
                    for g in gates["gates"]] + ["", f"**Verdict: {gates['message']}**"],
            }
            self._write_report(eid, base_res, scenarios, base_key, boot, baseline, extra,
                               summary_title="Validation segment (1x costs)")
            write_csvs(self._report_dir(eid), "walk_forward_oos", wf.oos_trades, wf.oos_equity)
            self.tracker.finish(eid, metrics={"walk_forward_oos": wf.oos_metrics,
                                              **{f"validation_{k}": v["metrics"] for k, v in scenarios.items()}},
                                gates=gates, verdict=gates["verdict"], summary=summary,
                                report_dir=str(self._report_dir(eid)), data_sources=base_res.data_sources)
            progress(1.0, gates["verdict"])
            return {"experiment_id": eid, **summary}
        except Exception as exc:
            self.tracker.fail(eid, f"{type(exc).__name__}: {exc}")
            raise

    # ----- the adaptive selector, walk-forward -----------------------------------------------------------------
    def selector_validation(self, req: SelectorRequest, ctx: JobContext | None = None,
                            created_by: str = "user") -> dict[str, Any]:
        """Walk-forward test of adaptive selection against each strategy on its own.

        In every window, evidence per strategy and regime comes only from that window's
        training part; the selector then trades the following unseen part. The same test
        parts are traded by each strategy alone, so the comparison is like for like.
        """
        from app.backtesting.walk_forward import make_windows
        from app.research.adaptive import AdaptiveSelector, evidence_from_trades

        s = self.settings
        progress: Progress = ctx.progress if ctx else _noop
        pairs, tf, confirm = self._scope(req.pairs, req.timeframe, req.exchange)
        ds = datasets.register(self.db, self.market, exchange=req.exchange, pairs=pairs, timeframe=tf,
                               split=s.backtest.split)
        research = ds.window("research")
        candles = datasets.load_candles(self.market, req.exchange, pairs, tf, end=ds.validation_end_ts)
        approved = self.tracker.approved()
        strategies = {name: build(name, (approved.get(name) or {}).get("params") or {}) for name in STRATEGIES}
        eid = self.tracker.create(kind="selector_walk_forward", settings=s, request=req.model_dump(mode="json"),
                                  strategies=[st.version for st in strategies.values()], pairs=pairs, timeframe=tf,
                                  segment="research", created_by=created_by,
                                  label=req.label or "adaptive selector walk-forward", dataset_id=ds.id,
                                  start_ts=research[0], end_ts=research[1], seed=s.backtest.seed,
                                  hypothesis="Choosing among strategies by regime-specific out-of-sample evidence beats "
                                             "running any single strategy everywhere.")
        try:
            rules = self._rules(req.exchange)
            cash = s.portfolio.initial_cash
            base = BacktestSpec(pairs=pairs, timeframe=tf, confirm_timeframes=confirm, exchange=req.exchange,
                                start=research[0], end=research[1], initial_cash=cash, label=eid)
            views = prepare_views(candles, base, s)
            windows = make_windows(research[0], research[1], tf, s.backtest.walk_forward_windows, s.backtest.train_ratio)
            sel_trades: list[Any] = []
            sel_equity: list[dict[str, Any]] = []
            single: dict[str, list[Any]] = {n: [] for n in strategies}
            rows = []
            carry = cash
            steps = len(windows) * (len(strategies) * 3 + 1)
            done = 0
            for w in windows:
                evidence = {}
                for name, strat in strategies.items():
                    tr_spec = BacktestSpec(**{**base.__dict__, "strategy": name, "params": strat.params_dict,
                                              "start": w.train_start, "end": w.train_end})
                    tr = run_backtest(tr_spec, candles, s, strategies={name: strat}, precomputed=views, rules=rules,
                                      keep_decisions=False)
                    trm = compute_metrics(tr.trades, tr.equity, initial_cash=cash)
                    stress = run_backtest(BacktestSpec(**{**tr_spec.__dict__,
                                                           "cost_multiplier": s.selector.stress_multiplier}),
                                          candles, s, strategies={name: strat}, precomputed=views, rules=rules,
                                          keep_decisions=False)
                    stm = compute_metrics(stress.trades, stress.equity, initial_cash=cash)
                    evidence[name] = evidence_from_trades(
                        strat, "under evaluation", [t.to_dict() for t in tr.trades],
                        max_drawdown_pct=trm["max_drawdown_pct"], stress_expectancy_r=stm["expectancy_r"],
                        source=f"window {w.index} training part")
                    te_spec = BacktestSpec(**{**tr_spec.__dict__, "start": w.test_start, "end": w.test_end})
                    te = run_backtest(te_spec, candles, s, strategies={name: strat}, precomputed=views, rules=rules,
                                      keep_decisions=False)
                    single[name].extend(te.trades)
                    done += 3
                    progress(done / steps, f"window {w.index}: evidence for {name}")
                selector = AdaptiveSelector(evidence, s.selector, require_approved=False)
                sel_spec = BacktestSpec(**{**base.__dict__, "start": w.test_start, "end": w.test_end,
                                           "initial_cash": carry})
                res = run_backtest(sel_spec, candles, s, strategies=strategies, selector=selector, precomputed=views,
                                   rules=rules, keep_decisions=False)
                done += 1
                progress(done / steps, f"window {w.index}: adaptive selection")
                m = compute_metrics(res.trades, res.equity, initial_cash=carry)
                sel_trades.extend(res.trades)
                sel_equity.extend(res.equity)
                if res.equity:
                    carry = float(res.equity[-1]["equity"])
                eligible_any = {r: [n for n, ev in evidence.items()
                                    if any(row["eligible"] for row in selector.table(r) if row["strategy"] == n)]
                                for r in ("trend_up", "range", "high_volatility_breakout")}
                rows.append({"window": w.index, "test": [w.test_start, w.test_end], "trades": m["trades"],
                             "net_return_pct": m["net_return_pct"], "expectancy_r": m["expectancy_r"],
                             "eligible_by_regime": eligible_any})
            sel_m = compute_metrics(sel_trades, sel_equity, initial_cash=cash)
            from app.backtesting.metrics import trades_frame as _tf
            comparison = {}
            for name, trades in single.items():
                df = _tf(trades)
                comparison[name] = {"trades": len(df), "expectancy_r": round(float(df["r_multiple"].mean()), 4)
                                    if len(df) else None, "net_pnl": round(float(df["net_pnl"].sum()), 2)}
            boot = bootstrap(trades_frame(sel_trades), samples=s.backtest.bootstrap_samples, seed=s.backtest.seed)
            self.tracker.store_trades(eid, "selector_oos", "research", tf, sel_trades)
            self.tracker.store_equity(eid, "selector_oos", sel_equity, None)
            profitable = [r for r in rows if r["trades"] >= s.backtest.min_trades_per_window and
                          (r["net_return_pct"] or 0) > 0]
            gates = evaluate_gates(oos=sel_m, stress=None, stability=None,
                                   window_ratio=len(profitable) / len(rows) if rows else None, bootstrap=boot,
                                   cfg=s.gates, risk=s.risk)
            summary = {"trades": sel_m["trades"], "net_return_pct": sel_m["net_return_pct"],
                       "expectancy_r": sel_m["expectancy_r"], "profit_factor": sel_m["profit_factor"],
                       "max_drawdown_pct": sel_m["max_drawdown_pct"], "windows": rows, "single_strategies": comparison,
                       "verdict": gates["verdict"], "verdict_message": gates["message"],
                       "failed_gates": gates["failed"]}
            directory = self._report_dir(eid)
            write_csvs(directory, "selector_oos", sel_trades, sel_equity)
            exp = self.tracker.get(eid)
            exp["status"] = "completed"
            sections = {
                "Adaptive selection, out of sample": metrics_table(sel_m),
                "Each strategy alone on the same test windows": ["| Strategy | Trades | Expectancy (R) | Net PnL |",
                                                                 "|---|---:|---:|---:|"] + [
                    f"| {k} | {v['trades']} | {v['expectancy_r']} | {v['net_pnl']} |" for k, v in comparison.items()],
                "Windows": ["| Window | Test period | Trades | Net return | Eligible strategies by regime |",
                            "|---|---|---:|---:|---|"] + [
                    f"| {r['window']} | {iso(r['test'][0])} to {iso(r['test'][1])} | {r['trades']} | "
                    f"{r['net_return_pct']}% | {r['eligible_by_regime']} |" for r in rows],
                "Acceptance gates": ["| Gate | Passed | Value |", "|---|---|---|"] + [
                    f"| {g['gate']} | {'yes' if g['passed'] else 'NO'} | {g['value']} |" for g in gates["gates"]] + [
                    "", f"**Verdict: {gates['message']}**",
                    "Parameter stability and cost stress are measured per strategy by the validation protocol."],
            }
            write_markdown(directory, markdown(exp, sections), {"selector_oos": sel_m, "single": comparison})
            self.tracker.finish(eid, metrics={"selector_oos": sel_m}, gates=gates, verdict=gates["verdict"],
                                summary=summary, report_dir=str(directory))
            progress(1.0, gates["verdict"])
            return {"experiment_id": eid, **summary}
        except Exception as exc:
            self.tracker.fail(eid, f"{type(exc).__name__}: {exc}")
            raise

    # ----- the frozen test ----------------------------------------------------------------------------------
    def final_test(self, req: FinalTestRequest, ctx: JobContext | None = None, actor: str = "user") -> dict[str, Any]:
        if actor != "user":
            raise InvalidRequest("only a person can open the frozen test set")
        s = self.settings
        progress: Progress = ctx.progress if ctx else _noop
        version = self.tracker.version(req.version_id)
        if version["status"] not in {"validated", "approved"}:
            raise Conflict("only a version that passed the validation protocol may be evaluated on the frozen test "
                           "set; every look at the test set reduces its value")
        pairs, tf, confirm = self._scope(req.pairs, req.timeframe, req.exchange)
        ds = datasets.register(self.db, self.market, exchange=req.exchange, pairs=pairs, timeframe=tf,
                               split=s.backtest.split)
        datasets.verify_test_unchanged(ds, self.market)
        test = ds.window("test")
        strat = build(version["strategy"], version["params"])
        eid = self.tracker.create(kind="final_test", settings=s, request=req.model_dump(mode="json"),
                                  strategies=[strat.version], pairs=pairs, timeframe=tf, segment="test", created_by=actor,
                                  label=f"final test of {strat.version}", hypothesis=strat.hypothesis,
                                  dataset_id=ds.id, start_ts=test[0], end_ts=test[1], seed=s.backtest.seed,
                                  uses_test_set=True)
        accesses = datasets.record_test_access(self.db, ds, eid, [strat.version], req.reason, actor)
        try:
            candles = datasets.load_candles(self.market, req.exchange, pairs, tf, end=ds.end_ts)
            rules = self._rules(req.exchange)
            cash = s.portfolio.initial_cash
            scenarios: dict[str, Any] = {}
            base_res = None
            for mult in (1.0, s.gates.stress_multiplier):
                progress(0.1 if mult == 1.0 else 0.6, f"test segment at {mult:g}x costs")
                spec = self._spec(strat.name, strat.params_dict, pairs, tf, confirm, req.exchange, test, mult, cash,
                                  False, eid)
                res = run_backtest(spec, candles, s, rules=rules)
                name = "base" if mult == 1.0 else f"cost_{mult:g}x"
                scenarios[name] = {"metrics": compute_metrics(res.trades, res.equity, initial_cash=cash,
                                                              benchmark=res.benchmark), "multiplier": mult,
                                   "stats": dict(res.stats.rejections), "notes": res.notes}
                self.tracker.store_trades(eid, name, "test", tf, res.trades)
                self.tracker.store_equity(eid, name, res.equity, res.benchmark)
                if mult == 1.0:
                    base_res = res
            assert base_res is not None
            boot = bootstrap(trades_frame(base_res.trades), samples=s.backtest.bootstrap_samples, seed=s.backtest.seed)
            stress_key = f"cost_{s.gates.stress_multiplier:g}x"
            # Stability and walk-forward consistency were measured during validation; the test set
            # is only used for the out-of-sample performance itself.
            gates = evaluate_gates(oos=scenarios["base"]["metrics"], stress=scenarios[stress_key]["metrics"],
                                   stability=_from_validation(version, "parameter_stability"),
                                   window_ratio=_from_validation(version, "walk_forward_consistency"),
                                   bootstrap=boot, cfg=s.gates, risk=s.risk)
            summary = self._summary(scenarios, "base", boot, None, base_res)
            summary.update({"version_id": version["id"], "verdict": gates["verdict"], "verdict_message": gates["message"],
                            "failed_gates": gates["failed"], "test_set_accesses": accesses})
            extra = {"Frozen test set": [
                f"Dataset {ds.id}; test candles {iso(test[0])} to {iso(test[1])}; fingerprint verified unchanged.",
                f"This is access number {accesses} to this test set. Reason given: {req.reason}",
                "Repeated evaluations on the same test set make it less informative; treat later results with care."],
                "Acceptance gates": ["| Gate | Passed | Value | Threshold |", "|---|---|---|---|"] + [
                    f"| {g['gate']} | {'yes' if g['passed'] else 'NO'} | {g['value']} | {g['threshold']} |"
                    for g in gates["gates"]] + ["", f"**Verdict: {gates['message']}**"]}
            self._write_report(eid, base_res, scenarios, "base", boot, None, extra,
                               summary_title="Frozen test segment (1x costs)")
            self.tracker.finish(eid, metrics={k: v["metrics"] for k, v in scenarios.items()}, gates=gates,
                                verdict=gates["verdict"], summary=summary, report_dir=str(self._report_dir(eid)),
                                data_sources=base_res.data_sources)
            progress(1.0, gates["verdict"])
            return {"experiment_id": eid, **summary}
        except Exception as exc:
            self.tracker.fail(eid, f"{type(exc).__name__}: {exc}")
            raise


def _from_validation(version: dict[str, Any], gate: str) -> float | None:
    """A value measured by the validation protocol (the test set is never used to measure it)."""
    for g in (version.get("validation") or {}).get("gates") or []:
        if g.get("gate") == gate:
            return g.get("value")
    return None


def guard_window(ds: datasets.Dataset, start: int, end: int) -> None:
    test_start, _ = ds.window("test")
    if end >= test_start:
        raise TestSetLocked("this window reaches into the frozen test segment; use the final test instead")
