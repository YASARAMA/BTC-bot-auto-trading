// Form for every kind of experiment. Each one runs as a background job and is stored with its
// exact configuration, data period and software version, whatever its outcome.
import { useEffect, useMemo, useState } from "react";
import { FlaskConical, Lock, Play } from "lucide-react";
import { JobProgress } from "../../components/domain/JobProgress";
import { ParamsEditor, changedParams, type ParamValues } from "../../components/domain/ParamsEditor";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Notice } from "../../components/ui/Feedback";
import { Field, Input, Segmented, Select, Switch } from "../../components/ui/Form";
import { post } from "../../lib/api";
import { fmtDateTime } from "../../lib/format";
import { useInventory, useStrategies } from "../../lib/queries";
import type { Job } from "../../lib/types";
import { useAction } from "../../lib/useAction";
import { usePersistent } from "../../lib/usePersistent";

type Kind = "backtest" | "validation" | "selector" | "final";

export function NewExperiment({ onCreated }: { onCreated: (experimentId: string) => void }) {
  const inventory = useInventory();
  const strategies = useStrategies();
  const [kind, setKind] = usePersistent<Kind>("lab.kind", "backtest");
  const [exchange, setExchange] = usePersistent("lab.exchange", "binance");
  const [timeframe, setTimeframe] = usePersistent("lab.timeframe", "1h");
  const [pairs, setPairs] = usePersistent<string[]>("lab.pairs", []);
  const [strategy, setStrategy] = usePersistent("lab.strategy", "trend_following");
  const [segment, setSegment] = usePersistent<"development" | "validation" | "research">("lab.segment", "development");
  const [mults, setMults] = usePersistent<number[]>("lab.mults", [1, 2, 3]);
  const [breakers, setBreakers] = useState(false);
  const [baseline, setBaseline] = useState(false);
  const [cash, setCash] = useState("");
  const [label, setLabel] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [params, setParams] = useState<ParamValues>({});
  const [versionId, setVersionId] = useState("");
  const [reason, setReason] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const series = inventory.data?.series ?? [];
  const exchanges = useMemo(() => Array.from(new Set(series.map((s) => s.exchange))).sort(), [series]);
  const exSeries = series.filter((s) => s.exchange === exchange);
  const availablePairs = Array.from(new Set(exSeries.map((s) => s.pair))).sort();
  const timeframes = Array.from(new Set(exSeries.filter((s) => !pairs.length || pairs.includes(s.pair)).map((s) => s.timeframe)));
  const chosen = pairs.filter((p) => availablePairs.includes(p));
  const spec = strategies.data?.find((s) => s.name === strategy)?.spec;
  const finalCandidates = (strategies.data ?? []).flatMap((s) => s.versions.filter((v) => v.status === "validated" || v.status === "approved"));

  useEffect(() => {
    if (exchanges.length && !exchanges.includes(exchange)) setExchange(exchanges[0]);
  }, [exchanges, exchange, setExchange]);
  useEffect(() => {
    if (availablePairs.length && !chosen.length) setPairs([availablePairs[0]]);
  }, [availablePairs, chosen.length, setPairs]);
  useEffect(() => {
    if (timeframes.length && !timeframes.includes(timeframe)) setTimeframe(timeframes[0]);
  }, [timeframes, timeframe, setTimeframe]);
  useEffect(() => setParams({}), [strategy]);

  const scope = { pairs: chosen, timeframe, exchange, label: label || null };
  const submit = useAction(
    async () => {
      if (kind === "backtest")
        return post<Job>("/api/backtests", {
          ...scope,
          strategy,
          params: spec ? changedParams(spec, params) : {},
          segment,
          cost_multipliers: mults.length ? mults : [1],
          initial_cash: cash ? Number(cash) : null,
          enforce_breakers: breakers,
          random_baseline: baseline,
          hypothesis: hypothesis || null,
        });
      if (kind === "validation")
        return post<Job>("/api/validations", { ...scope, strategy, params: spec ? changedParams(spec, params) : {}, hypothesis: hypothesis || null });
      if (kind === "selector") return post<Job>("/api/selector-validations", scope);
      return post<Job>("/api/final-tests", { version_id: Number(versionId), reason, pairs: chosen, timeframe, exchange });
    },
    { success: (j) => `${j.label} started`, onSuccess: (j) => setJobId(j.id), invalidate: [["jobs"]], errorTitle: "Could not start the experiment" },
  );

  const range = exSeries.filter((s) => chosen.includes(s.pair) && s.timeframe === timeframe);
  const canSubmit = chosen.length > 0 && !!timeframe && (kind !== "final" || (versionId !== "" && reason.trim().length >= 10));

  return (
    <Card title="New experiment" icon={<FlaskConical className="size-4" />} subtitle="Runs in the background; every run is recorded, including failures">
      <div className="space-y-4">
        <Segmented
          value={kind}
          onChange={setKind}
          options={[
            { value: "backtest", label: "Backtest", title: "One strategy on the development or validation segment, at several cost levels" },
            { value: "validation", label: "Validation", title: "The full protocol: sensitivity, walk-forward, cost stress, gates" },
            { value: "selector", label: "Selector WF", title: "Walk-forward test of the adaptive selector itself" },
            { value: "final", label: "Final test", title: "One look at the frozen test segment for a validated version" },
          ]}
        />
        <div className="grid grid-cols-2 gap-3">
          <Field label="Data (exchange)">
            <Select value={exchange} onChange={(e) => setExchange(e.target.value)} options={(exchanges.length ? exchanges : ["binance"]).map((x) => ({ value: x, label: x }))} />
          </Field>
          <Field label="Signal timeframe" hint="Higher confirmation timeframes are built from it">
            <Select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} options={(timeframes.length ? timeframes : [timeframe]).map((t) => ({ value: t, label: t }))} />
          </Field>
        </div>
        <Field group label="Pairs" hint={range.length ? range.map((s) => `${s.pair}: ${fmtDateTime(s.first_ts)} → ${fmtDateTime(s.last_ts)}, ${s.candles} candles${s.missing ? `, ${s.missing} missing` : ""}`).join(" · ") : "Stored local candles only"}>
          <div className="flex flex-wrap gap-1.5">
            {availablePairs.length === 0 && <span className="text-xs text-muted">No stored candles. Download or import data on the Market page first.</span>}
            {availablePairs.map((p) => {
              const on = chosen.includes(p);
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPairs(on ? chosen.filter((x) => x !== p) : [...chosen, p])}
                  className={`rounded-lg border px-2.5 py-1 text-xs font-medium ${on ? "border-accent bg-accent/15 text-text" : "border-border-strong bg-surface-2 text-muted hover:text-text"}`}
                >
                  {p}
                </button>
              );
            })}
          </div>
        </Field>

        {(kind === "backtest" || kind === "validation") && (
          <>
            <Field label="Strategy">
              <Select value={strategy} onChange={(e) => setStrategy(e.target.value)} options={(strategies.data ?? []).map((s) => ({ value: s.name, label: s.title }))} />
            </Field>
            {spec && (
              <details className="rounded-lg border border-border p-3">
                <summary className="cursor-pointer text-xs font-medium text-muted">Parameters (defaults unless changed; • marks tunables)</summary>
                <div className="mt-3">
                  <ParamsEditor spec={spec} values={params} onChange={setParams} />
                </div>
              </details>
            )}
          </>
        )}

        {kind === "backtest" && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Segment" hint="The test segment is reserved for the final test">
                <Select
                  value={segment}
                  onChange={(e) => setSegment(e.target.value as typeof segment)}
                  options={[
                    { value: "development", label: "Development (50%)" },
                    { value: "validation", label: "Validation (25%)" },
                    { value: "research", label: "Dev + validation" },
                  ]}
                />
              </Field>
              <Field label="Initial virtual cash">
                <Input type="number" min={1} value={cash} onChange={(e) => setCash(e.target.value)} placeholder="from settings" />
              </Field>
            </div>
            <Field label="Cost scenarios (fees, slippage and spread multiplied)" group>
              <div className="flex gap-1.5">
                {[1, 2, 3].map((m) => {
                  const on = mults.includes(m);
                  return (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setMults(on ? mults.filter((x) => x !== m) : [...mults, m].sort())}
                      className={`rounded-lg border px-3 py-1 text-xs font-medium ${on ? "border-accent bg-accent/15 text-text" : "border-border-strong bg-surface-2 text-muted"}`}
                    >
                      {m}x
                    </button>
                  );
                })}
              </div>
            </Field>
            <div className="flex flex-wrap gap-5">
              <Switch checked={breakers} onChange={setBreakers} label={<span className="text-xs">Enforce circuit breakers</span>} />
              <Switch checked={baseline} onChange={setBaseline} label={<span className="text-xs">Random-entry baseline (slower)</span>} />
            </div>
          </>
        )}

        {kind === "selector" && (
          <Notice>
            Walk-forward test of the adaptive selection itself: in every window the selector only knows the evidence of the earlier training part. With no strategy
            eligible it abstains, and the report says so.
          </Notice>
        )}

        {kind === "final" && (
          <>
            <Notice tone="warn" icon={<Lock className="size-3.5" />}>
              The frozen test segment (last 25%) is opened only for a version that passed validation. Every access is recorded with your reason and shown under
              Datasets; each look reduces the value of the test set, so use it once per final candidate.
            </Notice>
            <Field label="Validated or approved version">
              <Select
                value={versionId}
                onChange={(e) => setVersionId(e.target.value)}
                options={[{ value: "", label: finalCandidates.length ? "Choose a version" : "No validated versions yet" }, ...finalCandidates.map((v) => ({ value: String(v.id), label: `${v.version} (${v.status})` }))]}
              />
            </Field>
            <Field label="Reason for opening the test set (at least 10 characters)">
              <Input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} placeholder="e.g. final evaluation before approving for paper trading" />
            </Field>
          </>
        )}

        {kind !== "final" && (
          <div className="grid gap-3">
            <Field label="Label (optional)">
              <Input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={200} />
            </Field>
            {kind !== "selector" && (
              <Field label="Hypothesis (optional; recorded with the experiment)">
                <Input value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} maxLength={2000} />
              </Field>
            )}
          </div>
        )}

        <div className="flex justify-end">
          <Button variant="primary" icon={<Play className="size-4" />} loading={submit.isPending} disabled={!canSubmit} onClick={() => submit.mutate(undefined)}>
            Run {kind === "backtest" ? "backtest" : kind === "validation" ? "validation protocol" : kind === "selector" ? "selector walk-forward" : "final test"}
          </Button>
        </div>
        {jobId && (
          <JobProgress
            jobId={jobId}
            onClear={() => setJobId(null)}
            onDone={(job) => {
              const id = (job.result as { experiment_id?: string } | null)?.experiment_id;
              if (job.status === "completed" && id) onCreated(id);
            }}
          />
        )}
      </div>
    </Card>
  );
}
