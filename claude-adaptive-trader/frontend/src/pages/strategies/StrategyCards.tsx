// Each strategy with its rules, the evidence behind its current version and the version
// lifecycle: research -> validated (by the protocol) -> approved (by a person only).
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { BadgeCheck, Ban, FlaskConical, Plus } from "lucide-react";
import { RegimeBadge, VerdictBadge, VersionStatusBadge, regimeLabel } from "../../components/domain/Badges";
import { ParamsEditor, changedParams, type ParamValues } from "../../components/domain/ParamsEditor";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog, Dialog } from "../../components/ui/Dialog";
import { EmptyState, Notice, SkeletonRows } from "../../components/ui/Feedback";
import { Field, Input, Switch } from "../../components/ui/Form";
import { DataTable } from "../../components/ui/Table";
import { post } from "../../lib/api";
import { fmtDateTime, fmtNum, fmtPct, titleCase, toneClass } from "../../lib/format";
import { openExperiment } from "../../lib/navigation";
import { useStrategies } from "../../lib/queries";
import type { Job, StrategyInfo, StrategyVersion } from "../../lib/types";
import { useAction } from "../../lib/useAction";

export function StrategyCards() {
  const q = useStrategies();
  if (q.isLoading) return <SkeletonRows rows={10} />;
  return (
    <div className="space-y-4">
      <Notice>
        A version becomes <b>validated</b> only when the validation protocol passes every preliminary gate, and <b>approved</b> only by a person. The adaptive selector
        trades approved versions only. Approving a version that did not pass validation needs an explicit override and is labelled as such.
      </Notice>
      {(q.data ?? []).map((s) => (
        <StrategyCard key={s.name} s={s} />
      ))}
    </div>
  );
}

function StrategyCard({ s }: { s: StrategyInfo }) {
  const [newOpen, setNewOpen] = useState(false);
  const spec = s.spec;
  return (
    <Card
      title={<span className="flex flex-wrap items-center gap-2">{s.title} <span className="font-normal text-faint">{s.name}</span> {s.approved ? <Badge tone="up">approved: {s.approved.version.split("+").pop()}</Badge> : <Badge>no approved version</Badge>}</span>}
      subtitle={spec.hypothesis}
      actions={
        <Button size="sm" icon={<Plus className="size-3.5" />} onClick={() => setNewOpen(true)}>
          New version
        </Button>
      }
    >
      <div className="grid gap-4 xl:grid-cols-[1fr_1.2fr]">
        <div className="space-y-3 text-xs">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted">Trades in:</span>
            {spec.required_regimes.map((r) => (
              <RegimeBadge key={r} regime={r} />
            ))}
            <span className="ml-2 text-muted">Complexity {spec.complexity}</span>
            <span className="text-muted">· {spec.direction}</span>
          </div>
          <details>
            <summary className="cursor-pointer font-medium text-muted">Rules</summary>
            <div className="mt-2 space-y-2 leading-5">
              <RuleList title="Entry" items={spec.entry_rules} />
              <RuleList title="Exit" items={spec.exit_rules} />
              <RuleList title="Stop" items={[spec.stop_rule]} />
              <RuleList title="Target" items={[spec.target_rule]} />
              <p className="text-faint">{spec.required_data}</p>
            </div>
          </details>
          <details>
            <summary className="cursor-pointer font-medium text-muted">Default parameters</summary>
            <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-3">
              {Object.entries(spec.params).map(([k, v]) => (
                <span key={k} className="text-muted">
                  {titleCase(k)} <b className="num text-text">{String(v)}</b>
                  {spec.tunable.includes(k) && <span className="text-accent"> •</span>}
                </span>
              ))}
            </div>
          </details>
        </div>
        <EvidencePanel s={s} />
      </div>
      <div className="mt-4">
        <VersionsTable s={s} />
      </div>
      <NewVersionDialog s={s} open={newOpen} onClose={() => setNewOpen(false)} />
    </Card>
  );
}

function RuleList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="font-medium text-text">{title}</div>
      <ul className="list-disc pl-4 text-muted">
        {items.map((i) => (
          <li key={i}>{i}</li>
        ))}
      </ul>
    </div>
  );
}

function EvidencePanel({ s }: { s: StrategyInfo }) {
  const ev = s.evidence;
  if (!ev) {
    return (
      <EmptyState title="No out-of-sample evidence yet" compact>
        Run the validation protocol for a version. Its walk-forward out-of-sample trades become the evidence the selector uses, by regime.
      </EmptyState>
    );
  }
  const rows = Object.entries(ev.by_regime).map(([regime, e]) => ({ regime, ...e }));
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted">
        <span>
          Evidence for <b className="text-text">{s.evidence_version}</b> ({ev.status})
        </span>
        <span>max drawdown {fmtPct(ev.max_drawdown_pct, 2)}</span>
        <span>at 2x costs {fmtNum(ev.stress_expectancy_r, 3)} R</span>
        {ev.recent_trades > 0 && (
          <span>
            recent paper {ev.recent_trades} trades, {fmtNum(ev.recent_expectancy_r, 3)} R
          </span>
        )}
      </div>
      <DataTable
        rows={rows}
        rowKey={(r) => r.regime}
        dense
        empty={<EmptyState title="No out-of-sample trades" compact />}
        columns={[
          { key: "regime", header: "Regime", render: (r) => regimeLabel(r.regime) },
          { key: "t", header: "OOS trades", align: "right", render: (r) => r.trades },
          { key: "e", header: "Expectancy (R)", align: "right", render: (r) => <span className={toneClass(r.expectancy_r)}>{fmtNum(r.expectancy_r, 3)}</span> },
          { key: "lb", header: "Lower bound", align: "right", render: (r) => fmtNum(r.lower_bound_r, 3) },
          { key: "pf", header: "PF", align: "right", render: (r) => fmtNum(r.profit_factor, 2) },
          { key: "w", header: "Win rate", align: "right", render: (r) => fmtPct(r.win_rate_pct, 1) },
        ]}
      />
      <p className="text-[11px] text-faint">{ev.source}</p>
    </div>
  );
}

function VersionsTable({ s }: { s: StrategyInfo }) {
  const navigate = useNavigate();
  const [approve, setApprove] = useState<StrategyVersion | null>(null);
  const [override, setOverride] = useState(false);
  const [change, setChange] = useState<{ v: StrategyVersion; status: "rejected" | "retired" | "research" } | null>(null);
  const validate = useAction((v: StrategyVersion) => post<Job>("/api/validations", { strategy: v.strategy, params: v.params, hypothesis: v.hypothesis }), {
    success: (j) => `${j.label} started; follow it in the Backtesting Laboratory`,
    invalidate: [["jobs"], ["experiments"]],
  });
  const approveAction = useAction(({ v, note }: { v: StrategyVersion; note: string }) => post(`/api/strategies/versions/${v.id}/approve`, { note, override }), {
    success: "Version approved",
    invalidate: [["strategies"], ["selector"]],
    onSuccess: () => setApprove(null),
  });
  const statusAction = useAction(({ v, status, note }: { v: StrategyVersion; status: string; note: string }) => post(`/api/strategies/versions/${v.id}/status`, { status, note }), {
    success: "Version status changed",
    invalidate: [["strategies"], ["selector"]],
    onSuccess: () => setChange(null),
  });
  return (
    <>
      <DataTable
        rows={s.versions}
        rowKey={(v) => v.id}
        dense
        empty={<EmptyState title="No versions recorded yet" compact>A version is recorded the first time it is backtested or created here.</EmptyState>}
        columns={[
          { key: "v", header: "Version", render: (v) => <span className="block max-w-[300px] truncate font-medium" title={v.version}>{v.version}</span> },
          { key: "status", header: "Status", render: (v) => <VersionStatusBadge status={v.status} /> },
          { key: "created", header: "Created", render: (v) => <span title={v.created_by}>{fmtDateTime(v.created_at)}</span> },
          {
            key: "val",
            header: "Validation",
            render: (v) =>
              v.validation ? (
                <button type="button" className="inline-flex items-center gap-1" onClick={() => v.validation_experiment_id && openExperiment(navigate, v.validation_experiment_id)} title={v.validation.message}>
                  <VerdictBadge verdict={v.validation.verdict} />
                  {v.validation.failed.length > 0 && <span className="text-[11px] text-faint">{v.validation.failed.length} gate(s) failed</span>}
                </button>
              ) : (
                <span className="text-faint">not validated</span>
              ),
          },
          { key: "appr", header: "Approval", render: (v) => (v.approved_at ? <span className="block max-w-[240px] truncate text-muted" title={v.approval_note ?? ""}>{fmtDateTime(v.approved_at)} · {v.approval_note}</span> : <span className="text-faint">—</span>) },
          {
            key: "actions",
            header: "",
            align: "right",
            render: (v) => (
              <div className="flex justify-end gap-1">
                <Button size="sm" variant="ghost" icon={<FlaskConical className="size-3.5" />} onClick={() => validate.mutate(v)} title="Run the validation protocol on this version">
                  Validate
                </Button>
                {v.status !== "approved" && v.status !== "rejected" && v.status !== "retired" && (
                  <Button size="sm" variant={v.status === "validated" ? "success" : "ghost"} icon={<BadgeCheck className="size-3.5" />} onClick={() => { setOverride(false); setApprove(v); }}>
                    Approve
                  </Button>
                )}
                {v.status === "approved" && (
                  <Button size="sm" variant="ghost" onClick={() => setChange({ v, status: "retired" })}>
                    Retire
                  </Button>
                )}
                {(v.status === "research" || v.status === "validated") && (
                  <Button size="sm" variant="ghost" icon={<Ban className="size-3.5" />} onClick={() => setChange({ v, status: "rejected" })}>
                    Reject
                  </Button>
                )}
              </div>
            ),
          },
        ]}
      />
      <ConfirmDialog
        open={!!approve}
        onClose={() => setApprove(null)}
        title={`Approve ${approve?.version ?? ""} for paper trading?`}
        confirmLabel={approve?.status === "validated" ? "Approve" : "Approve with override"}
        danger={approve?.status !== "validated"}
        confirmDisabled={approve?.status !== "validated" && !override}
        busy={approveAction.isPending}
        requireNote="Approval note (recorded)"
        onConfirm={(note) => approve && approveAction.mutate({ v: approve, note })}
      >
        {approve?.status === "validated" ? (
          <p>This version passed every preliminary gate. Approval lets the adaptive selector use it in paper trading, in the regimes where it has enough evidence. It does not guarantee future profits.</p>
        ) : (
          <div className="space-y-2">
            <Notice tone="warn">This version has not passed the validation protocol. Approving it anyway is recorded as an OVERRIDE with your note.</Notice>
            <Switch checked={override} onChange={setOverride} label={<span className="text-xs">I understand; approve with an override</span>} />
          </div>
        )}
      </ConfirmDialog>
      <ConfirmDialog
        open={!!change}
        onClose={() => setChange(null)}
        title={`${change?.status === "retired" ? "Retire" : "Reject"} ${change?.v.version ?? ""}?`}
        confirmLabel={change?.status === "retired" ? "Retire" : "Reject"}
        danger
        busy={statusAction.isPending}
        requireNote="Reason (recorded)"
        onConfirm={(note) => change && statusAction.mutate({ v: change.v, status: change.status, note })}
      >
        <p>{change?.status === "retired" ? "A retired version is no longer used by the selector." : "A rejected version cannot be approved later."}</p>
      </ConfirmDialog>
    </>
  );
}

function NewVersionDialog({ s, open, onClose }: { s: StrategyInfo; open: boolean; onClose: () => void }) {
  const [values, setValues] = useState<ParamValues>({});
  const [hypothesis, setHypothesis] = useState("");
  const create = useAction(() => post<StrategyVersion>(`/api/strategies/${s.name}/versions`, { params: changedParams(s.spec, values), hypothesis: hypothesis || null }), {
    success: (v) => `Version ${v.version} recorded (status: ${v.status})`,
    invalidate: [["strategies"]],
    onSuccess: () => {
      setValues({});
      setHypothesis("");
      onClose();
    },
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`New version of ${s.title}`}
      width={720}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={create.isPending} onClick={() => create.mutate(undefined)}>Record version</Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-xs text-muted">A version is identified by its parameters. It starts in research status; validate it before it can be approved without an override.</p>
        <ParamsEditor spec={s.spec} values={values} onChange={setValues} />
        <Field label="Hypothesis (what you expect and why)">
          <Input value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} maxLength={2000} />
        </Field>
      </div>
    </Dialog>
  );
}
