// One stored experiment: verdict, gates, metrics per scenario, equity against buy & hold,
// walk-forward windows, sensitivity, per-regime results, trades and report files.
import { useEffect, useMemo, useState } from "react";
import { CircleAlert, Download, FileText, Sparkles } from "lucide-react";
import { DrawdownChart, EquityChart } from "../../components/charts/Charts";
import { ExperimentStatusBadge, KIND_LABEL, VerdictBadge, regimeLabel } from "../../components/domain/Badges";
import { scenarioLabel } from "../../components/domain/RunPicker";
import { GroupTable, MetricsList, TradesTable } from "../../components/domain/Tables";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState, ErrorState, Notice, SkeletonRows } from "../../components/ui/Feedback";
import { Select, Tabs } from "../../components/ui/Form";
import { Kpi } from "../../components/ui/Kpi";
import { DataTable } from "../../components/ui/Table";
import { post } from "../../lib/api";
import { fmtDate, fmtDateTime, fmtNum, fmtPct, titleCase, toneClass, toneOf } from "../../lib/format";
import { useExperiment, useExperimentEquity, useExperimentTrades, useResearchStatus } from "../../lib/queries";
import type { Experiment, Gate, Job } from "../../lib/types";
import { useAction } from "../../lib/useAction";

type Tab = "overview" | "gates" | "walkforward" | "regimes" | "trades" | "details";

export function ExperimentDetail({ id }: { id: string }) {
  const q = useExperiment(id);
  const exp = q.data;
  const [scenario, setScenario] = useState("");
  const [tab, setTab] = useState<Tab>("overview");
  useEffect(() => {
    if (!exp) return;
    const preferred = ["base", "walk_forward_oos", "selector_oos", "validation_base"].find((s) => exp.scenarios.includes(s)) ?? exp.scenarios[0] ?? "";
    setScenario((cur) => (cur && exp.scenarios.includes(cur) ? cur : preferred));
  }, [exp]);
  useEffect(() => setTab("overview"), [id]);
  const equity = useExperimentEquity(exp ? id : null, scenario);
  const trades = useExperimentTrades(exp && tab === "trades" ? id : null, scenario);

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!exp) return <Card><SkeletonRows rows={10} /></Card>;
  const m = exp.metrics?.[scenario];
  const s = exp.summary ?? {};
  const tabs: { value: Tab; label: string }[] = [
    { value: "overview", label: "Overview" },
    ...(exp.gates ? [{ value: "gates" as Tab, label: `Gates (${exp.gates.gates.filter((g) => g.passed).length}/${exp.gates.gates.length})` }] : []),
    ...(s.walk_forward || s.windows ? [{ value: "walkforward" as Tab, label: "Walk-forward" }] : []),
    { value: "regimes", label: "By regime" },
    { value: "trades", label: "Trades" },
    { value: "details", label: "Configuration" },
  ];

  return (
    <div className="space-y-4">
      <Card
        title={<span className="flex flex-wrap items-center gap-2">{exp.label ?? exp.id} <ExperimentStatusBadge status={exp.status} /> {exp.verdict && <VerdictBadge verdict={exp.verdict} />} {exp.uses_test_set && <Badge tone="warn">frozen test set</Badge>}</span>}
        subtitle={`${KIND_LABEL[exp.kind] ?? exp.kind} · ${exp.id} · ${exp.pairs.join(", ")} · ${exp.timeframe} · ${fmtDate(exp.start_ts)} → ${fmtDate(exp.end_ts)} (${exp.segment})`}
      >
        <Downloads exp={exp} />
        {exp.status === "failed" && (
          <Notice tone="down" icon={<CircleAlert className="size-3.5" />}>
            This experiment failed: {exp.error}. The failure is kept in the record.
          </Notice>
        )}
        {s.verdict_message && (
          <Notice tone={exp.verdict === "passed_preliminary_gates" ? "up" : "warn"} className="mb-3">
            {s.verdict_message}
          </Notice>
        )}
        {exp.status === "completed" && (
          <div className="flex flex-wrap items-center gap-3">
            {exp.scenarios.length > 0 && (
              <div className="w-64">
                <Select value={scenario} onChange={(e) => setScenario(e.target.value)} options={exp.scenarios.map((x) => ({ value: x, label: scenarioLabel(x) }))} />
              </div>
            )}
            <span className="text-xs text-muted">Strategies: {exp.strategies.join(", ")}</span>
            <AssistantReview id={exp.id} />
          </div>
        )}
      </Card>

      {exp.status === "completed" && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 2xl:grid-cols-8">
            <Kpi label="Trades" value={m ? String(m.trades) : "—"} />
            <Kpi label="Net return" value={fmtPct(m?.net_return_pct, 2, true)} tone={toneOf(m?.net_return_pct)} />
            <Kpi label="Expectancy" value={m?.expectancy_r === null || m?.expectancy_r === undefined ? "—" : `${fmtNum(m.expectancy_r, 3)} R`} tone={toneOf(m?.expectancy_r)} />
            <Kpi label="Profit factor" value={fmtNum(m?.profit_factor, 2)} />
            <Kpi label="Win rate" value={fmtPct(m?.win_rate_pct, 1)} />
            <Kpi label="Max drawdown" value={fmtPct(m?.max_drawdown_pct, 2)} tone={m?.max_drawdown_pct ? "down" : undefined} />
            <Kpi label="Sharpe" value={m?.sharpe === null || m?.sharpe === undefined ? "n/a" : fmtNum(m.sharpe, 2)} hint="Daily returns, annualised; shown only with 30+ daily returns." />
            <Kpi label="Buy & hold" value={fmtPct(m?.benchmark_return_pct, 2, true)} tone={toneOf(m?.benchmark_return_pct)} sub="same pairs, same costs" />
          </div>
          <Card bodyClassName="p-0">
            <div className="px-4 pt-2">
              <Tabs value={tab} onChange={setTab} tabs={tabs} />
            </div>
            <div className="space-y-4 p-4">
              {tab === "overview" && <OverviewTab exp={exp} scenario={scenario} points={equity.data?.points} loading={equity.isLoading} />}
              {tab === "gates" && exp.gates && <GatesTable gates={exp.gates.gates} />}
              {tab === "walkforward" && <WalkForwardTab exp={exp} />}
              {tab === "regimes" && (
                <div className="grid gap-4 xl:grid-cols-2">
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">By regime at entry</h3>
                    <GroupTable groups={m?.by_regime} label="Regime" format={regimeLabel} />
                  </div>
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">By higher-timeframe environment</h3>
                    <GroupTable groups={m?.by_environment} label="Environment" format={(k) => k} />
                  </div>
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">By exit reason</h3>
                    <GroupTable groups={m?.by_exit_reason} label="Exit" format={(k) => k} />
                  </div>
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">Time spent in each regime (candles)</h3>
                    <RegimeShare regimes={s.regimes} />
                  </div>
                </div>
              )}
              {tab === "trades" && <TradesTable trades={trades.data} loading={trades.isLoading} maxHeight={600} />}
              {tab === "details" && <DetailsTab exp={exp} />}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function Downloads({ exp }: { exp: Experiment }) {
  if (!exp.files.length) return null;
  return (
    <div className="mb-3 flex flex-wrap gap-1.5">
      {exp.files.includes("report.md") && (
        <a href={`/api/experiments/${exp.id}/report.md`} download>
          <Button size="sm" icon={<FileText className="size-3.5" />}>Report</Button>
        </a>
      )}
      {exp.files
        .filter((f) => f !== "report.md")
        .map((f) => (
          <a key={f} href={`/api/experiments/${exp.id}/files/${f}`} download>
            <Button size="sm" variant="ghost" icon={<Download className="size-3.5" />}>{f}</Button>
          </a>
        ))}
    </div>
  );
}

function AssistantReview({ id }: { id: string }) {
  const status = useResearchStatus();
  const review = useAction(() => post<Job>("/api/research/review", { experiment_id: id }), {
    success: "The research assistant is reviewing this experiment; its notes appear under Strategy Management",
    invalidate: [["jobs"]],
  });
  const available = !!status.data?.available;
  return (
    <Button
      size="sm"
      className="ml-auto"
      icon={<Sparkles className="size-3.5" />}
      disabled={!available}
      loading={review.isPending}
      title={available ? "Ask the optional research assistant for a written review" : `Research assistant unavailable: ${status.data?.problems.join("; ") ?? ""}`}
      onClick={() => review.mutate(undefined)}
    >
      Assistant review
    </Button>
  );
}

function OverviewTab({ exp, scenario, points, loading }: { exp: Experiment; scenario: string; points: import("../../lib/types").EquityPoint[] | undefined; loading: boolean }) {
  const m = exp.metrics?.[scenario];
  const s = exp.summary ?? {};
  const dd = useMemo(() => {
    let peak = -Infinity;
    return (points ?? []).map((p) => {
      peak = Math.max(peak, p.equity);
      return { ts: p.ts, drawdown_pct: peak > 0 ? (1 - p.equity / peak) * 100 : 0 };
    });
  }, [points]);
  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Equity · {scenarioLabel(scenario)} · dashed: buy &amp; hold</h3>
          {loading ? <SkeletonRows rows={6} /> : <EquityChart points={points} initial={m?.initial_cash} height={260} />}
          <h3 className="mb-1 mt-3 text-xs font-semibold uppercase tracking-wider text-muted">Drawdown</h3>
          <DrawdownChart points={dd} height={130} />
        </div>
        <div className="space-y-4">
          {s.stress && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">Cost stress{exp.kind === "validation" ? " · validation segment" : ""}</h3>
              <DataTable
                rows={Object.entries(s.stress).map(([k, v]) => ({ k, ...v }))}
                rowKey={(r) => r.k}
                dense
                columns={[
                  { key: "k", header: "Scenario", render: (r) => scenarioLabel(r.k) },
                  { key: "t", header: "Trades", align: "right", render: (r) => r.trades },
                  { key: "ret", header: "Net return", align: "right", render: (r) => <span className={toneClass(r.net_return_pct)}>{fmtPct(r.net_return_pct, 2, true)}</span> },
                  { key: "exp", header: "Expectancy (R)", align: "right", render: (r) => fmtNum(r.expectancy_r, 3) },
                ]}
              />
            </div>
          )}
          <div className="rounded-lg border border-border p-3 text-xs">
            <div className="mb-1 font-medium">Uncertainty</div>
            {s.bootstrap?.expectancy_r_ci ? (
              <p className="text-muted">
                95% bootstrap interval of expectancy:{" "}
                <b className="num text-text">
                  {fmtNum(s.bootstrap.expectancy_r_ci[0], 3)} R to {fmtNum(s.bootstrap.expectancy_r_ci[1], 3)} R
                </b>
                {s.bootstrap.expectancy_r_ci[0] <= 0 && " — the interval includes zero or less, so the edge is not established."}
              </p>
            ) : (
              <p className="text-muted">Not enough trades for a bootstrap interval.</p>
            )}
            {s.random_baseline && (
              <p className="mt-1 text-muted">
                The strategy beat <b className="num text-text">{fmtPct(s.random_baseline.beats_pct, 0)}</b> of random-entry runs with the same structure.
              </p>
            )}
          </div>
          {(s.notes?.length ?? 0) > 0 && (
            <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-faint">
              {s.notes!.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
      {m ? <MetricsList m={m} /> : <EmptyState title="No metrics for this scenario" />}
    </>
  );
}

export function GatesTable({ gates }: { gates: Gate[] }) {
  return (
    <>
      <DataTable
        rows={gates}
        rowKey={(g) => g.gate}
        columns={[
          { key: "gate", header: "Gate", render: (g) => <span className="font-medium">{titleCase(g.gate)}</span> },
          { key: "passed", header: "Result", render: (g) => <Badge tone={g.passed ? "up" : "down"}>{g.passed ? "passed" : "failed"}</Badge> },
          { key: "value", header: "Value", align: "right", render: (g) => fmtGateValue(g.value) },
          { key: "thr", header: "Required", align: "right", render: (g) => String(g.threshold) },
          { key: "detail", header: "What it measures", render: (g) => <span className="block max-w-[420px] whitespace-normal text-muted">{g.detail}</span> },
        ]}
      />
      <p className="mt-2 text-[11px] text-faint">Preliminary screening rules, not proof: passing them qualifies a strategy for a forward test on paper, nothing more.</p>
    </>
  );
}

function fmtGateValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return fmtNum(v, Math.abs(v) >= 100 ? 0 : 3);
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  return String(v);
}

function WalkForwardTab({ exp }: { exp: Experiment }) {
  const s = exp.summary ?? {};
  const wf = s.walk_forward;
  return (
    <div className="space-y-4">
      {wf && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Kpi label="OOS trades" value={String(wf.oos.trades ?? "—")} />
            <Kpi label="OOS net return" value={fmtPct(wf.oos.net_return_pct, 2, true)} tone={toneOf(wf.oos.net_return_pct)} />
            <Kpi label="OOS expectancy" value={`${fmtNum(wf.oos.expectancy_r, 3)} R`} tone={toneOf(wf.oos.expectancy_r)} />
            <Kpi label="Profitable windows" value={fmtPct((wf.profitable_window_ratio ?? 0) * 100, 0)} />
            <Kpi label="OOS at 2x costs" value={`${fmtNum(wf.stress_oos_expectancy_r, 3)} R`} tone={toneOf(wf.stress_oos_expectancy_r)} />
          </div>
          <DataTable
            rows={wf.windows}
            rowKey={(w) => w.window}
            dense
            columns={[
              { key: "w", header: "Window", render: (w) => w.window },
              { key: "p", header: "Out-of-sample period", render: (w) => `${fmtDate(w.test[0])} → ${fmtDate(w.test[1])}` },
              { key: "t", header: "Trades", align: "right", render: (w) => w.trades },
              { key: "r", header: "Net return", align: "right", render: (w) => <span className={toneClass(w.net_return_pct)}>{fmtPct(w.net_return_pct, 2, true)}</span> },
              { key: "e", header: "Expectancy (R)", align: "right", render: (w) => fmtNum(w.expectancy_r, 3) },
              { key: "pf", header: "Profit factor", align: "right", render: (w) => fmtNum(w.profit_factor, 2) },
            ]}
          />
        </>
      )}
      {s.sensitivity && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
            Parameter sensitivity on the development segment · stability {fmtNum(s.sensitivity.stability, 2)}
          </h3>
          <DataTable
            rows={s.sensitivity.neighbours}
            rowKey={(r, i) => `${r.param}-${i}`}
            dense
            empty={<EmptyState title="No tunable parameters were varied" />}
            columns={[
              { key: "p", header: "Parameter", render: (r) => titleCase(r.param) },
              { key: "v", header: "Value (base)", render: (r) => `${String(r.value)} (${String(r.base_value)})` },
              { key: "t", header: "Trades", align: "right", render: (r) => r.trades },
              { key: "e", header: "Expectancy (R)", align: "right", render: (r) => <span className={toneClass(r.expectancy_r)}>{fmtNum(r.expectancy_r, 3)}</span> },
            ]}
          />
        </div>
      )}
      {s.windows && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">Selector windows</h3>
          <DataTable
            rows={s.windows}
            rowKey={(w) => w.window}
            dense
            columns={[
              { key: "w", header: "Window", render: (w) => w.window },
              { key: "p", header: "Out-of-sample period", render: (w) => `${fmtDate(w.test[0])} → ${fmtDate(w.test[1])}` },
              { key: "t", header: "Trades", align: "right", render: (w) => w.trades },
              { key: "r", header: "Net return", align: "right", render: (w) => fmtPct(w.net_return_pct, 2, true) },
              { key: "e", header: "Eligible by regime", render: (w) => <span className="block max-w-[420px] whitespace-normal text-muted">{JSON.stringify(w.eligible_by_regime ?? {})}</span> },
            ]}
          />
        </div>
      )}
      {s.single_strategies && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">Each strategy alone on the same windows</h3>
          <DataTable
            rows={Object.entries(s.single_strategies).map(([k, v]) => ({ k, ...v }))}
            rowKey={(r) => r.k}
            dense
            columns={[
              { key: "k", header: "Strategy", render: (r) => titleCase(r.k) },
              { key: "t", header: "Trades", align: "right", render: (r) => r.trades },
              { key: "e", header: "Expectancy (R)", align: "right", render: (r) => fmtNum(r.expectancy_r, 3) },
              { key: "n", header: "Net PnL", align: "right", render: (r) => fmtNum(r.net_pnl, 2) },
            ]}
          />
        </div>
      )}
    </div>
  );
}

function RegimeShare({ regimes }: { regimes: Record<string, Record<string, number>> | undefined }) {
  const entries = Object.entries(regimes ?? {});
  if (!entries.length) return <EmptyState title="No regime data" compact />;
  return (
    <div className="space-y-3">
      {entries.map(([pair, counts]) => {
        const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
        return (
          <div key={pair} className="text-xs">
            <div className="mb-1 font-medium">{pair}</div>
            <div className="flex h-3 overflow-hidden rounded-full">
              {Object.entries(counts).map(([r, n]) => (
                <div key={r} title={`${regimeLabel(r)}: ${n} candles`} style={{ width: `${(n / total) * 100}%`, background: REGIME_BAR[r] ?? "#5d687a" }} />
              ))}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 text-faint">
              {Object.entries(counts).map(([r, n]) => (
                <span key={r}>
                  {regimeLabel(r)} {fmtPct((n / total) * 100, 0)}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const REGIME_BAR: Record<string, string> = { trend_up: "#2ebd85", trend_down: "#f6465d", range: "#4ea8ff", high_volatility_breakout: "#f0b90b", uncertain: "#5d687a" };

function DetailsTab({ exp }: { exp: Experiment }) {
  const rows: [string, React.ReactNode][] = [
    ["Experiment id", exp.id],
    ["Kind", KIND_LABEL[exp.kind] ?? exp.kind],
    ["Created", `${fmtDateTime(exp.created_at)} by ${exp.created_by}`],
    ["Finished", fmtDateTime(exp.finished_at)],
    ["Strategy versions", exp.strategies.join(", ")],
    ["Hypothesis", exp.hypothesis ?? "—"],
    ["Pairs / timeframe", `${exp.pairs.join(", ")} · ${exp.timeframe}`],
    ["Segment", exp.segment],
    ["Period", `${fmtDateTime(exp.start_ts)} → ${fmtDateTime(exp.end_ts)}`],
    ["Dataset", exp.dataset_id ?? "—"],
    ["Uses frozen test set", exp.uses_test_set ? "yes (access recorded)" : "no"],
    ["Software version", exp.software_version],
    ["Configuration hash", exp.config_hash],
    ["Random seed", exp.seed ?? "—"],
  ];
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="break-all text-text">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="space-y-3 text-xs">
        <div>
          <div className="mb-1 font-medium">Data sources (candles per source in the period)</div>
          <pre className="max-h-40 overflow-auto rounded-md border border-border bg-surface-2/50 p-2 text-[11px] text-muted">{JSON.stringify(exp.data_sources ?? {}, null, 2)}</pre>
        </div>
        <div>
          <div className="mb-1 font-medium">Request</div>
          <pre className="max-h-60 overflow-auto rounded-md border border-border bg-surface-2/50 p-2 text-[11px] text-muted">{JSON.stringify(exp.request ?? {}, null, 2)}</pre>
        </div>
        <a href={`/api/experiments/${exp.id}/config`} target="_blank" rel="noreferrer" className="text-accent hover:underline">
          Full configuration snapshot (JSON)
        </a>
      </div>
    </div>
  );
}
