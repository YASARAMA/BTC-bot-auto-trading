import { useMemo } from "react";
import { fmtDateTime } from "../../lib/format";
import { useAnalyticsRuns } from "../../lib/queries";
import type { AnalyticsRun } from "../../lib/types";
import { Field, Select } from "../ui/Form";

// Choose a paper session or a completed backtest experiment (and its scenario) as a data source.
export function RunPicker({ value, onChange, scenario, onScenario, allowCurrent = true }: {
  value: string;
  onChange: (v: string) => void;
  scenario?: string;
  onScenario?: (s: string) => void;
  allowCurrent?: boolean;
}) {
  const runs = useAnalyticsRuns();
  const list = runs.data ?? [];
  const selected = useMemo(() => list.find((r) => key(r) === value), [list, value]);
  const options = [
    ...(allowCurrent ? [{ value: "", label: "Current paper session" }] : []),
    ...list.map((r) => ({ value: key(r), label: `${r.label} · ${fmtDateTime(r.created_at)}${r.uses_test_set ? " · TEST SET" : ""}` })),
  ];
  return (
    <div className="flex min-w-0 flex-wrap gap-3">
      <Field label="Run" className="min-w-[260px] flex-1">
        <Select value={value} onChange={(e) => onChange(e.target.value)} options={options} />
      </Field>
      {onScenario && selected && selected.source === "backtest" && selected.scenarios.length > 0 && (
        <Field label="Scenario" className="w-44">
          <Select value={scenario ?? "base"} onChange={(e) => onScenario(e.target.value)} options={selected.scenarios.map((s) => ({ value: s, label: scenarioLabel(s) }))} />
        </Field>
      )}
    </div>
  );
}

export function key(r: AnalyticsRun): string {
  return `${r.source}|${r.run_id}`;
}

export function parseRun(v: string): { source?: string; run_id?: string } {
  if (!v) return {};
  const [source, run_id] = v.split("|");
  return { source, run_id };
}

export function scenarioLabel(s: string): string {
  if (s === "base") return "Base costs (1x)";
  if (s === "walk_forward_oos") return "Walk-forward out of sample";
  if (s === "selector_oos") return "Selector out of sample";
  if (s === "validation_base") return "Validation segment (1x)";
  const v = s.match(/^validation_cost_(.+)x$/);
  if (v) return `Validation segment (costs ×${v[1]})`;
  const m = s.match(/^cost_(.+)x$/);
  return m ? `Costs ×${m[1]}` : s;
}
