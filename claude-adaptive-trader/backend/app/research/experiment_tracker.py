"""Experiment records and the strategy version lifecycle.

Every backtest, walk-forward, validation and final test is an experiment - including the
ones that fail or find nothing. Each stores the exact configuration, the data period and
sources, the strategy versions and the software version that produced it.

Strategy versions move through ``research -> validated -> approved`` (or ``rejected`` /
``retired``). ``validated`` is set only by the predefined validation protocol when every gate
passes. ``approved`` is set only by a person, and only an approved version can be chosen by
the adaptive selector for paper trading.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.core.clock import now_ms
from app.core.errors import Conflict, InvalidRequest, NotFound
from app.core.hashing import stable_hash
from app.core.version import software_version
from app.monitoring.logging import get_logger, log_event
from app.storage.database import Database, from_json, to_json
from app.strategies.registry import build

log = get_logger("experiments")

JSON_FIELDS = ("strategies", "pairs", "request", "config_snapshot", "data_sources", "metrics", "gates", "summary")


def new_id(prefix: str = "exp") -> str:
    return f"{prefix}-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class ExperimentTracker:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ----- experiments --------------------------------------------------------------------------
    def create(self, *, kind: str, settings: Settings, request: dict[str, Any], strategies: list[str], pairs: list[str],
               timeframe: str, segment: str, created_by: str = "user", label: str | None = None,
               hypothesis: str | None = None, dataset_id: str | None = None, start_ts: int | None = None,
               end_ts: int | None = None, seed: int | None = None, uses_test_set: bool = False,
               parent_id: str | None = None, experiment_id: str | None = None) -> str:
        eid = experiment_id or new_id()
        snapshot = settings.printable()
        self.db.execute(
            "INSERT INTO experiments (id, created_at, kind, status, label, hypothesis, created_by, strategies, pairs,"
            " timeframe, segment, dataset_id, start_ts, end_ts, request, config_snapshot, config_hash,"
            " software_version, seed, uses_test_set, parent_id) VALUES"
            " (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, now_ms(), kind, label, hypothesis, created_by, to_json(strategies), to_json(pairs), timeframe,
             segment, dataset_id, start_ts, end_ts, to_json(request), to_json(snapshot), stable_hash(snapshot),
             software_version(), seed, 1 if uses_test_set else 0, parent_id))
        return eid

    def finish(self, eid: str, *, metrics: dict[str, Any] | None = None, gates: dict[str, Any] | None = None,
               verdict: str | None = None, summary: dict[str, Any] | None = None, report_dir: str | None = None,
               data_sources: dict[str, Any] | None = None) -> None:
        self.db.execute(
            "UPDATE experiments SET status = 'completed', finished_at = ?, metrics = ?, gates = ?, verdict = ?,"
            " summary = ?, report_dir = ?, data_sources = COALESCE(?, data_sources) WHERE id = ?",
            (now_ms(), to_json(metrics) if metrics is not None else None, to_json(gates) if gates is not None else None,
             verdict, to_json(summary) if summary is not None else None, report_dir,
             to_json(data_sources) if data_sources is not None else None, eid))

    def fail(self, eid: str, error: str) -> None:
        # Failed experiments are kept: a crash is a result too.
        self.db.execute("UPDATE experiments SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                        (now_ms(), error[:4000], eid))

    def get(self, eid: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM experiments WHERE id = ?", (eid,))
        if not row:
            raise NotFound(f"experiment {eid} not found")
        for f in JSON_FIELDS:
            row[f] = from_json(row.get(f), None)
        row["uses_test_set"] = bool(row["uses_test_set"])
        return row

    def list(self, *, kind: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where, params = ("WHERE kind = ?", [kind]) if kind else ("", [])
        rows = self.db.query(
            f"SELECT id, created_at, finished_at, kind, status, label, created_by, strategies, pairs, timeframe,"
            f" segment, dataset_id, start_ts, end_ts, verdict, error, uses_test_set, parent_id, summary FROM experiments"
            f" {where} ORDER BY created_at DESC LIMIT ?", [*params, limit])
        for r in rows:
            for f in ("strategies", "pairs", "summary"):
                r[f] = from_json(r.get(f), None)
            r["uses_test_set"] = bool(r["uses_test_set"])
        return rows

    def store_trades(self, eid: str, scenario: str, segment: str, timeframe: str, trades: list[Any]) -> None:
        rows = []
        for t in trades:
            rows.append(("backtest", eid, scenario, segment, t.position_key, t.pair, timeframe, t.strategy,
                         t.strategy_version, t.regime, t.environment, t.entry_ts, t.exit_ts, t.entry_price,
                         t.exit_price, t.qty, t.stop_price, t.take_profit, t.gross_pnl, t.fees, t.slippage_cost,
                         t.net_pnl, t.return_pct, t.initial_risk, t.r_multiple, t.exit_reason, t.entry_reason,
                         t.mae_pct, t.mfe_pct, t.bars_held))
        self.db.executemany(
            "INSERT OR IGNORE INTO trades (source, run_id, scenario, segment, position_key, pair, timeframe, strategy,"
            " strategy_version, regime, environment, entry_ts, exit_ts, entry_price, exit_price, qty, stop_price,"
            " take_profit, gross_pnl, fees, slippage_cost, net_pnl, return_pct, initial_risk, r_multiple, exit_reason,"
            " entry_reason, mae_pct, mfe_pct, bars_held) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def store_equity(self, eid: str, scenario: str, equity: list[dict[str, Any]], benchmark: list[float] | None,
                     max_points: int = 2000) -> None:
        n = len(equity)
        if not n:
            return
        stride = max(1, n // max_points)
        idx = list(range(0, n, stride))
        if idx[-1] != n - 1:
            idx.append(n - 1)
        rows = []
        for i in idx:
            p = equity[i]
            rows.append(("backtest", eid, scenario, int(p["ts"]), p["equity"], p["cash"], p.get("exposure", 0.0),
                         p.get("open_risk", 0.0), p.get("drawdown_pct", 0.0),
                         benchmark[i] if benchmark and i < len(benchmark) else None))
        self.db.executemany(
            "INSERT OR REPLACE INTO equity_curve (source, run_id, scenario, ts, equity, cash, exposure, open_risk,"
            " drawdown_pct, benchmark) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    # ----- strategy versions ---------------------------------------------------------------------------
    def ensure_version(self, strategy: str, params: dict[str, Any] | None, *, created_by: str = "user",
                       hypothesis: str | None = None, parent_id: int | None = None) -> dict[str, Any]:
        strat = build(strategy, params or {})
        row = self.db.query_one("SELECT * FROM strategy_versions WHERE strategy = ? AND params_hash = ?",
                                (strategy, strat.params_hash))
        if row is None:
            self.db.execute(
                "INSERT INTO strategy_versions (strategy, version, params, params_hash, status, created_at, created_by,"
                " parent_id, hypothesis) VALUES (?, ?, ?, ?, 'research', ?, ?, ?, ?)",
                (strategy, strat.version, to_json(strat.params_dict), strat.params_hash, now_ms(), created_by,
                 parent_id, hypothesis or strat.hypothesis))
            row = self.db.query_one("SELECT * FROM strategy_versions WHERE strategy = ? AND params_hash = ?",
                                    (strategy, strat.params_hash))
        return self._version_row(row)

    def _version_row(self, row: dict[str, Any] | None) -> dict[str, Any]:
        if row is None:
            raise NotFound("strategy version not found")
        out = dict(row)
        out["params"] = from_json(out["params"], {})
        out["validation"] = from_json(out.get("validation"), None)
        return out

    def version(self, version_id: int) -> dict[str, Any]:
        return self._version_row(self.db.query_one("SELECT * FROM strategy_versions WHERE id = ?", (version_id,)))

    def versions(self, strategy: str | None = None) -> list[dict[str, Any]]:
        if strategy:
            rows = self.db.query("SELECT * FROM strategy_versions WHERE strategy = ? ORDER BY created_at DESC",
                                 (strategy,))
        else:
            rows = self.db.query("SELECT * FROM strategy_versions ORDER BY created_at DESC")
        return [self._version_row(r) for r in rows]

    def record_validation(self, version_id: int, result: dict[str, Any], experiment_id: str) -> dict[str, Any]:
        v = self.version(version_id)
        passed = bool(result.get("passed"))
        new_status = v["status"]
        if v["status"] in {"research", "validated"}:
            new_status = "validated" if passed else "research"
        self.db.execute("UPDATE strategy_versions SET validation = ?, validation_experiment_id = ?, status = ?"
                        " WHERE id = ?", (to_json(result), experiment_id, new_status, version_id))
        log_event(log, 20, f"validation of {v['version']}: {result.get('verdict')}", experiment=experiment_id)
        return self.version(version_id)

    def approve(self, version_id: int, *, actor: str, note: str, override: bool = False) -> dict[str, Any]:
        if actor != "user":
            # Only a person may approve. The research assistant and automated jobs cannot.
            raise InvalidRequest("only a person can approve a strategy version")
        v = self.version(version_id)
        if v["status"] == "approved":
            raise Conflict("this version is already approved")
        if v["status"] in {"rejected", "retired"}:
            raise Conflict(f"a {v['status']} version cannot be approved")
        if v["status"] != "validated" and not override:
            raise Conflict("this version has not passed the validation protocol; approving it needs an explicit "
                           "override with a reason")
        if len(note.strip()) < 3:
            raise InvalidRequest("approval needs a note explaining the decision")
        with self.db.transaction():
            # One approved version per strategy: the previous one is retired.
            self.db.execute("UPDATE strategy_versions SET status = 'retired' WHERE strategy = ? AND status = 'approved'",
                            (v["strategy"],))
            label = note if not override else f"OVERRIDE (did not pass validation): {note}"
            self.db.execute("UPDATE strategy_versions SET status = 'approved', approved_at = ?, approved_by = ?,"
                            " approval_note = ? WHERE id = ?", (now_ms(), actor, label, version_id))
        log_event(log, 30 if override else 20, f"strategy version {v['version']} approved by {actor}",
                  override=override, note=note)
        return self.version(version_id)

    def set_status(self, version_id: int, status: str, *, actor: str, note: str) -> dict[str, Any]:
        if status not in {"rejected", "retired", "research"}:
            raise InvalidRequest("status can only be set to rejected, retired or research here")
        if actor != "user":
            raise InvalidRequest("only a person can change a version's status")
        v = self.version(version_id)
        self.db.execute("UPDATE strategy_versions SET status = ?, notes = ? WHERE id = ?",
                        (status, f"{status} by {actor}: {note}", version_id))
        log_event(log, 20, f"strategy version {v['version']} set to {status}", note=note)
        return self.version(version_id)

    def approved(self) -> dict[str, dict[str, Any]]:
        rows = self.db.query("SELECT * FROM strategy_versions WHERE status = 'approved'")
        return {r["strategy"]: self._version_row(r) for r in rows}
