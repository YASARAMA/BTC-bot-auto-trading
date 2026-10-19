"""Optional Claude research assistant.

What it may do: read completed experiment reports, metrics and (redacted) logs, and write
research notes - reviews, explanations, suspected bugs - and *proposals* for new parameter
experiments.

What it cannot do, by construction rather than by instruction:

* It has no tools. Its only output is text (or a structured proposal) stored as a note.
* It cannot place, cancel or modify orders: nothing in this module touches the broker.
* It cannot change settings or risk limits: the settings store and the version approval
  refuse any actor other than a person, and proposals can only name strategy parameters,
  which are validated against each strategy's own bounds.
* It cannot see or touch the frozen test set: final evaluation requires a person, and only
  research-segment experiments are offered to it.
* Nothing it proposes runs until a person approves it; an approved proposal becomes a new
  strategy version in ``research`` status that must still pass the validation protocol and a
  separate human approval before the adaptive selector may use it.

The platform works fully without it: it is off by default and needs an ``ANTHROPIC_API_KEY``
in the environment and a model id in the settings.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from pydantic import BaseModel, Field

from app.config import Settings
from app.core.clock import now_ms
from app.core.errors import ExchangeUnavailable, InvalidRequest, NotFound, TraderError
from app.monitoring.logging import get_logger, log_event
from app.research.experiment_tracker import ExperimentTracker
from app.storage.database import Database, from_json, to_json
from app.strategies.registry import STRATEGIES, build

log = get_logger("research")

SYSTEM_PROMPT = """You are the research assistant of a paper-trading research platform for crypto spot markets.
Your job is careful, skeptical analysis of backtest and walk-forward evidence. You never claim or imply that a
strategy will be profitable; you look for weaknesses: too few trades, dependence on one period or one trade,
regime concentration, cost sensitivity, parameter fragility, possible look-ahead or accounting bugs, and gaps
between in-sample and out-of-sample results. Results you are shown are simulated and use fixed cost assumptions.
You cannot change settings, risk limits, data or orders, and you never see the frozen test set; a person reviews
everything you write. Be concise and concrete. Use plain text with short paragraphs and bullet lists."""


class ParamChange(BaseModel):
    name: str = Field(description="exact parameter name of the strategy")
    value: float = Field(description="proposed value")


class ExperimentProposal(BaseModel):
    title: str = Field(description="short name for the experiment")
    hypothesis: str = Field(description="what you expect and why, falsifiable")
    param_changes: list[ParamChange] = Field(description="parameter changes from the current version; may be empty")
    rationale: str = Field(description="which evidence in the reports motivates this")
    risk_of_overfitting: str = Field(description="how this experiment could fool us, and how to check")


class ProposalSet(BaseModel):
    proposals: list[ExperimentProposal]


class AssistantUnavailable(TraderError):
    code = "assistant_unavailable"
    status_code = 409


class ClaudeResearcher:
    def __init__(self, settings: Callable[[], Settings], db: Database, tracker: ExperimentTracker, *,
                 client_factory: Callable[[], Any] | None = None) -> None:
        self._settings = settings
        self.db = db
        self.tracker = tracker
        self._client_factory = client_factory
        self._client: Any = None

    # ----- availability ----------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        s = self._settings().research
        try:
            import anthropic  # noqa: F401
            sdk = True
        except ImportError:
            sdk = False
        key = bool(os.environ.get("ANTHROPIC_API_KEY")) or self._client_factory is not None
        problems = []
        if not s.claude_enabled:
            problems.append("disabled in settings (research.claude_enabled)")
        if not s.model.strip():
            problems.append("no model id set (research.model)")
        if not key:
            problems.append("ANTHROPIC_API_KEY is not set in the backend environment")
        if not sdk:
            problems.append("the anthropic Python package is not installed")
        return {"available": not problems, "problems": problems, "enabled": s.claude_enabled,
                "model": s.model or None, "api_key_present": key, "sdk_installed": sdk,
                "limits": ["cannot place or change orders", "cannot change settings or risk limits",
                           "cannot see or touch the frozen test set", "every proposal needs human approval"]}

    def _require(self) -> Any:
        st = self.status()
        if not st["available"]:
            raise AssistantUnavailable("the research assistant is not available: " + "; ".join(st["problems"]))
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                import anthropic

                self._client = anthropic.Anthropic()
        return self._client

    # ----- calls --------------------------------------------------------------------------------------
    def _request(self, call: Callable[..., Any], **kwargs: Any) -> Any:
        """One API call with adaptive thinking; retried once without it for models that do not support it."""
        import anthropic

        try:
            return call(thinking={"type": "adaptive"}, **kwargs)
        except anthropic.BadRequestError as exc:
            if "thinking" not in str(exc).lower():
                raise
            return call(**kwargs)

    def _ask(self, user: str) -> tuple[str, dict[str, Any]]:
        client = self._require()
        s = self._settings().research
        import anthropic

        try:
            response = self._request(client.messages.create, model=s.model, max_tokens=s.max_output_tokens,
                                     system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user}])
        except anthropic.AuthenticationError:
            raise AssistantUnavailable("the Anthropic API rejected the key") from None
        except anthropic.RateLimitError:
            raise AssistantUnavailable("rate limited by the Anthropic API; try again later") from None
        except anthropic.APIStatusError as exc:
            raise AssistantUnavailable(f"Anthropic API error {exc.status_code}: {exc.message}") from None
        except anthropic.APIConnectionError:
            raise ExchangeUnavailable("the Anthropic API could not be reached") from None
        meta = self._meta(response)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return (f"The model declined this request (category: {getattr(details, 'category', None)}).", meta)
        text = "\n\n".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
        if response.stop_reason == "max_tokens":
            text += "\n\n[truncated at the configured output limit]"
        return text, meta

    def _meta(self, response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        return {"model": getattr(response, "model", None), "stop_reason": getattr(response, "stop_reason", None),
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "request_id": getattr(response, "_request_id", None)}

    # ----- research tasks ----------------------------------------------------------------------------------
    def _research_experiment(self, eid: str) -> dict[str, Any]:
        exp = self.tracker.get(eid)
        if exp["uses_test_set"]:
            raise InvalidRequest("the assistant is not shown final-test experiments; it must never see the test set")
        if exp["status"] != "completed":
            raise InvalidRequest("only completed experiments can be reviewed")
        return exp

    def _report_text(self, exp: dict[str, Any], limit: int = 40_000) -> str:
        path = self._settings().reports_path / exp["id"] / "report.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = json.dumps({"summary": exp.get("summary"), "gates": exp.get("gates")}, default=str)
        return text[:limit]

    def _note(self, *, kind: str, title: str, body: str, experiment_id: str | None, payload: dict[str, Any] | None,
              status: str = "recorded", source: str = "claude") -> dict[str, Any]:
        nid = self.db.insert(
            "INSERT INTO research_notes (created_at, kind, source, experiment_id, title, body, payload, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now_ms(), kind, source, experiment_id, title[:300], body, to_json(payload) if payload else None, status))
        return self.note(nid)

    def review_experiment(self, eid: str) -> dict[str, Any]:
        self._require()
        exp = self._research_experiment(eid)
        prompt = ("Review this completed research experiment. Explain what the results do and do not show, list "
                  "the most likely weaknesses or implementation bugs to check, and say whether the evidence supports "
                  "continuing, changing or abandoning the hypothesis.\n\n<report>\n" + self._report_text(exp) +
                  "\n</report>")
        text, meta = self._ask(prompt)
        log_event(log, 20, f"assistant reviewed {eid}", **{k: v for k, v in meta.items() if k != "request_id"})
        return self._note(kind="review", title=f"Review of {exp.get('label') or eid}", body=text, experiment_id=eid,
                          payload={"meta": meta})

    def explain_difference(self, a: str, b: str) -> dict[str, Any]:
        self._require()
        ea, eb = self._research_experiment(a), self._research_experiment(b)
        prompt = ("Explain the most important differences in results between these two experiments and the likely "
                  "causes (strategy version, period, regimes, costs, sample size). Be specific about uncertainty."
                  f"\n\n<experiment id=\"{a}\">\n{self._report_text(ea, 20_000)}\n</experiment>\n\n"
                  f"<experiment id=\"{b}\">\n{self._report_text(eb, 20_000)}\n</experiment>")
        text, meta = self._ask(prompt)
        return self._note(kind="explanation", title=f"Why {a} and {b} differ", body=text, experiment_id=a,
                          payload={"compared_with": b, "meta": meta})

    def review_logs(self, hours: int = 24) -> dict[str, Any]:
        self._require()
        since = now_ms() - hours * 3_600_000
        rows = self.db.query("SELECT ts, level, source, message FROM logs WHERE ts >= ? AND level IN"
                             " ('WARNING', 'ERROR', 'CRITICAL') ORDER BY ts DESC LIMIT 300", (since,))
        if not rows:
            return self._note(kind="log_review", title="Log review", body="No warnings or errors in the period.",
                              experiment_id=None, payload=None, source="system")
        lines = "\n".join(f"{r['ts']} {r['level']} {r['source']}: {r['message']}" for r in rows)
        text, meta = self._ask("These are the warnings and errors logged by the platform (secrets are masked). "
                               "Group them by likely cause, rank by impact on research validity and paper-trading "
                               "safety, and suggest what to check first.\n\n<logs>\n" + lines[:40_000] + "\n</logs>")
        return self._note(kind="log_review", title=f"Log review ({hours}h)", body=text, experiment_id=None,
                          payload={"entries": len(rows), "meta": meta})

    def propose_experiments(self, strategy: str, *, count: int = 3, experiment_ids: list[str] | None = None) -> list[dict[str, Any]]:
        self._require()
        if strategy not in STRATEGIES:
            raise InvalidRequest(f"unknown strategy {strategy}")
        current = self.tracker.approved().get(strategy)
        base = build(strategy, (current or {}).get("params") or {})
        ids = experiment_ids or [e["id"] for e in self.tracker.list(limit=50)
                                 if e["status"] == "completed" and not e["uses_test_set"]
                                 and any(str(v).startswith(f"{strategy}@") for v in (e.get("strategies") or []))][:3]
        reports = "\n\n".join(f"<experiment id=\"{i}\">\n{self._report_text(self._research_experiment(i), 15_000)}"
                              f"\n</experiment>" for i in ids)
        schema = base.Params.model_json_schema()
        prompt = (f"Propose at most {count} new experiments for the strategy `{strategy}`. Each must change only the "
                  f"strategy parameters listed in the schema below, within their bounds, and must be motivated by the "
                  f"evidence. Prefer fewer, more decisive experiments; propose none if the evidence does not justify "
                  f"any.\n\nStrategy hypothesis: {base.hypothesis}\nCurrent parameters: {json.dumps(base.params_dict)}"
                  f"\nParameter schema: {json.dumps(schema)}\n\n{reports or 'No completed experiments yet.'}")
        client = self._require()
        s = self._settings().research
        import anthropic

        try:
            response = self._request(client.messages.parse, model=s.model, max_tokens=s.max_output_tokens,
                                     system=SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}],
                                     output_format=ProposalSet)
        except anthropic.APIStatusError as exc:
            raise AssistantUnavailable(f"Anthropic API error {exc.status_code}: {exc.message}") from None
        except anthropic.APIConnectionError:
            raise ExchangeUnavailable("the Anthropic API could not be reached") from None
        if response.stop_reason == "refusal":
            return [self._note(kind="proposal", title="Proposal request declined",
                               body="The model declined this request.", experiment_id=ids[0] if ids else None,
                               payload={"meta": self._meta(response)})]
        parsed: ProposalSet | None = getattr(response, "parsed_output", None)
        if parsed is None:
            raise AssistantUnavailable("the model did not return proposals in the expected format")
        notes = []
        for prop in parsed.proposals[:count]:
            params, problems = self._validate_params(base, prop.param_changes)
            body = (f"Hypothesis: {prop.hypothesis}\n\nRationale: {prop.rationale}\n\n"
                    f"Risk of overfitting: {prop.risk_of_overfitting}")
            payload = {"strategy": strategy, "base_version": base.version, "params": params,
                       "changes": [c.model_dump() for c in prop.param_changes], "problems": problems,
                       "meta": self._meta(response)}
            notes.append(self._note(kind="proposal", title=prop.title, body=body,
                                    experiment_id=ids[0] if ids else None, payload=payload,
                                    status="pending_approval" if not problems else "rejected"))
        log_event(log, 20, f"assistant proposed {len(notes)} experiment(s) for {strategy}")
        return notes

    @staticmethod
    def _validate_params(base: Any, changes: list[ParamChange]) -> tuple[dict[str, Any], list[str]]:
        params = dict(base.params_dict)
        problems = []
        fields = base.Params.model_fields
        for c in changes:
            if c.name not in fields:
                problems.append(f"{c.name} is not a parameter of {base.name}")
                continue
            current = params[c.name]
            if isinstance(current, bool):
                params[c.name] = bool(c.value)
            elif isinstance(current, int):
                if abs(c.value - round(c.value)) > 1e-9:
                    problems.append(f"{c.name} must be a whole number")
                    continue
                params[c.name] = int(round(c.value))
            else:
                params[c.name] = float(c.value)
        if not problems:
            try:
                build(base.name, params)
            except InvalidRequest as exc:
                problems.append(exc.message)
        return params, problems

    # ----- notes and human decisions -----------------------------------------------------------------------
    def note(self, nid: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM research_notes WHERE id = ?", (nid,))
        if not row:
            raise NotFound(f"note {nid} not found")
        row["payload"] = from_json(row.get("payload"), None)
        return row

    def notes(self, *, status: str | None = None, kind: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where, params = ["1 = 1"], []
        if status:
            where.append("status = ?")
            params.append(status)
        if kind:
            where.append("kind = ?")
            params.append(kind)
        rows = self.db.query(f"SELECT * FROM research_notes WHERE {' AND '.join(where)} ORDER BY created_at DESC"
                             " LIMIT ?", [*params, limit])
        for r in rows:
            r["payload"] = from_json(r.get("payload"), None)
        return rows

    def add_user_note(self, title: str, body: str, experiment_id: str | None = None) -> dict[str, Any]:
        return self._note(kind="journal", title=title, body=body, experiment_id=experiment_id, payload=None,
                          source="user")

    def decide(self, nid: int, *, approve: bool, actor: str, note: str) -> dict[str, Any]:
        if actor != "user":
            raise InvalidRequest("only a person can approve or reject a proposal")
        n = self.note(nid)
        if n["kind"] != "proposal" or n["status"] != "pending_approval":
            raise InvalidRequest("only a pending proposal can be decided")
        version = None
        if approve:
            payload = n["payload"] or {}
            version = self.tracker.ensure_version(payload["strategy"], payload["params"],
                                                  created_by="assistant proposal, approved by user",
                                                  hypothesis=n["body"][:2000])
        self.db.execute("UPDATE research_notes SET status = ?, decided_by = ?, decided_at = ?, decision_note = ?"
                        " WHERE id = ?", ("approved" if approve else "rejected", actor, now_ms(), note, nid))
        log_event(log, 20, f"proposal {nid} {'approved' if approve else 'rejected'}", note=note,
                  version=(version or {}).get("version"))
        out = self.note(nid)
        out["version"] = version
        return out
