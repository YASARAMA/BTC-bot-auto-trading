// Backtesting laboratory: run experiments, browse every stored result (failed ones included)
// and audit the frozen datasets and each access to their test segment.
import { Database, FlaskConical, Lock } from "lucide-react";
import { ExperimentStatusBadge, KIND_LABEL, VerdictBadge } from "../components/domain/Badges";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState } from "../components/ui/Feedback";
import { Segmented } from "../components/ui/Form";
import { DataTable } from "../components/ui/Table";
import { fmtDate, fmtDateTime, fmtNum, fmtPct, toneClass } from "../lib/format";
import { useDatasets, useExperiments } from "../lib/queries";
import { usePersistent } from "../lib/usePersistent";
import { ExperimentDetail } from "./backtesting/ExperimentDetail";
import { NewExperiment } from "./backtesting/NewExperiment";

export function BacktestingPage() {
  const [selected, setSelected] = usePersistent<string | null>("lab.selected", null);
  return (
    <div className="space-y-4">
      <div className="grid items-start gap-4 xl:grid-cols-[420px_1fr]">
        <NewExperiment onCreated={setSelected} />
        <ExperimentList selected={selected} onSelect={setSelected} />
      </div>
      {selected ? <ExperimentDetail id={selected} /> : <EmptyState title="Select an experiment to see its results" icon={<FlaskConical className="size-6" />} />}
      <Datasets />
    </div>
  );
}

function ExperimentList({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const [kind, setKind] = usePersistent("lab.listKind", "");
  const q = useExperiments(kind || undefined);
  return (
    <Card
      title="Experiments"
      subtitle="Every run is kept with its configuration, data period and software version"
      actions={
        <Segmented
          size="sm"
          value={kind}
          onChange={setKind}
          options={[
            { value: "", label: "All" },
            { value: "backtest", label: "Backtests" },
            { value: "validation", label: "Validations" },
            { value: "selector_walk_forward", label: "Selector" },
            { value: "final_test", label: "Final" },
          ]}
        />
      }
    >
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <DataTable
          rows={q.data}
          loading={q.isLoading}
          rowKey={(e) => e.id}
          dense
          maxHeight={560}
          onRowClick={(e) => onSelect(e.id)}
          rowClassName={(e) => (e.id === selected ? "bg-accent/10" : undefined)}
          empty={<EmptyState title="No experiments yet">Run a backtest with the form. Results, including failed runs and &ldquo;no reliable edge&rdquo; verdicts, are listed here.</EmptyState>}
          columns={[
            { key: "created", header: "Created", render: (e) => fmtDateTime(e.created_at), sort: (e) => e.created_at },
            {
              key: "label",
              header: "Experiment",
              render: (e) => (
                <div className="max-w-[280px]">
                  <div className="truncate font-medium" title={e.label ?? e.id}>{e.label ?? e.id}</div>
                  <div className="truncate text-[11px] text-muted">
                    {KIND_LABEL[e.kind] ?? e.kind} · {e.pairs.join(", ")} {e.timeframe}
                    {e.uses_test_set && <span className="ml-1 text-warn">· test set</span>}
                  </div>
                </div>
              ),
            },
            { key: "result", header: "Result", render: (e) => (e.status !== "completed" ? <ExperimentStatusBadge status={e.status} /> : e.verdict ? <VerdictBadge verdict={e.verdict} /> : <Badge>completed</Badge>) },
            { key: "trades", header: "Trades", align: "right", render: (e) => e.summary?.trades ?? "—", sort: (e) => e.summary?.trades ?? null },
            {
              key: "ret",
              header: "Net return",
              align: "right",
              render: (e) => <span className={toneClass(e.summary?.net_return_pct)}>{fmtPct(e.summary?.net_return_pct, 2, true)}</span>,
              sort: (e) => e.summary?.net_return_pct ?? null,
            },
            { key: "exp", header: "Exp. (R)", align: "right", render: (e) => fmtNum(e.summary?.expectancy_r, 3), sort: (e) => e.summary?.expectancy_r ?? null },
          ]}
        />
      )}
    </Card>
  );
}

function Datasets() {
  const q = useDatasets();
  return (
    <Card title="Datasets and the frozen test segment" icon={<Database className="size-4" />} subtitle="Chronological split fixed at registration; the test segment is fingerprinted and every access is logged">
      <DataTable
        rows={q.data}
        loading={q.isLoading}
        rowKey={(d) => d.id}
        dense
        empty={<EmptyState title="No datasets registered yet">A dataset is registered the first time an experiment runs on a pair set and timeframe.</EmptyState>}
        columns={[
          { key: "id", header: "Dataset", render: (d) => <span className="font-medium">{d.id}</span> },
          { key: "data", header: "Data", render: (d) => `${d.exchange} · ${d.pairs.join(", ")} · ${d.timeframe}` },
          { key: "dev", header: "Development", render: (d) => `${fmtDate(d.segments.development.start)} → ${fmtDate(d.segments.development.end)}` },
          { key: "val", header: "Validation", render: (d) => `${fmtDate(d.segments.validation.start)} → ${fmtDate(d.segments.validation.end)}` },
          { key: "test", header: <span className="inline-flex items-center gap-1"><Lock className="size-3" /> Test (frozen)</span>, render: (d) => `${fmtDate(d.segments.test.start)} → ${fmtDate(d.segments.test.end)}` },
          { key: "fp", header: "Fingerprint", render: (d) => <span className="text-faint" title={d.test_fingerprint}>{d.test_fingerprint.slice(0, 12)}…</span> },
          {
            key: "acc",
            header: "Test accesses",
            render: (d) =>
              d.test_accesses.length ? (
                <span className="text-warn" title={d.test_accesses.map((a) => `${fmtDateTime(a.ts)} ${a.actor}: ${a.reason} (${a.experiment_id})`).join("\n")}>
                  {d.test_accesses.length} (last {fmtDateTime(d.test_accesses[d.test_accesses.length - 1].ts)})
                </span>
              ) : (
                <span className="text-up">never opened</span>
              ),
          },
        ]}
      />
    </Card>
  );
}
