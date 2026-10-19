// Structured logs (secrets are redacted before they are stored) and system diagnostics.
import { useState } from "react";
import { Activity, Database, HardDrive, Search, ShieldCheck } from "lucide-react";
import { EngineBadge } from "../components/domain/Badges";
import { Badge, type Tone } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/Feedback";
import { Field, Input, Segmented, Select, Switch, Tabs } from "../components/ui/Form";
import { DataTable } from "../components/ui/Table";
import { fmtAgo, fmtCompact, fmtDateTime, fmtNum, titleCase } from "../lib/format";
import { useDiagnostics, useLogs } from "../lib/queries";
import type { LogRow } from "../lib/types";
import { usePersistent } from "../lib/usePersistent";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const LEVEL_TONE: Record<string, Tone> = { DEBUG: "neutral", INFO: "info", WARNING: "warn", ERROR: "down", CRITICAL: "down" };

export function LogsPage() {
  const [tab, setTab] = usePersistent<"logs" | "diagnostics">("logs.tab", "logs");
  return (
    <div className="space-y-4">
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "logs", label: "Logs" }, { value: "diagnostics", label: "Diagnostics" }]} />
      {tab === "logs" ? <LogsView /> : <DiagnosticsView />}
    </div>
  );
}

function LogsView() {
  const [level, setLevel] = usePersistent("logs.level", "INFO");
  const [source, setSource] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(200);
  const [live, setLive] = usePersistent("logs.live", true);
  const [open, setOpen] = useState<LogRow | null>(null);
  const q = useLogs({ level, source: source || undefined, q: query || undefined, limit }, live);
  const d = q.data;
  return (
    <Card
      title="Structured logs"
      subtitle="Newest first. API keys and tokens are redacted before anything is written."
      actions={<Switch checked={live} onChange={setLive} label={<span className="text-xs">Live</span>} />}
    >
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Segmented
          value={level}
          onChange={setLevel}
          options={LEVELS.slice(0, 4).map((l, i) => {
            const n = d ? LEVELS.slice(i).reduce((sum, x) => sum + (d.counts[x] ?? 0), 0) : null;
            return { value: l, label: `${l === "DEBUG" ? "All" : `${titleCase(l.toLowerCase())}+`}${n !== null ? ` · ${n}` : ""}` };
          })}
        />
        <Field label="Source" className="w-48">
          <Select value={source} onChange={(e) => setSource(e.target.value)} options={[{ value: "", label: "All sources" }, ...(d?.sources ?? []).map((s) => ({ value: s, label: s }))]} />
        </Field>
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(search.trim());
          }}
        >
          <Field label="Message contains" className="w-64">
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="e.g. rejected" />
          </Field>
          <Button type="submit" icon={<Search className="size-3.5" />}>
            Search
          </Button>
        </form>
      </div>
      {q.isError && !d ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <>
          <DataTable
            rows={d?.rows}
            loading={q.isLoading}
            rowKey={(r) => r.id}
            dense
            maxHeight={620}
            onRowClick={setOpen}
            empty={<EmptyState title="No log entries match" />}
            columns={[
              { key: "ts", header: "Time", render: (r) => fmtDateTime(r.ts, true) },
              { key: "level", header: "Level", render: (r) => <Badge tone={LEVEL_TONE[r.level] ?? "neutral"}>{r.level}</Badge> },
              { key: "source", header: "Source", render: (r) => <span className="text-muted">{r.source}</span> },
              { key: "msg", header: "Message", render: (r) => <span className="block max-w-[760px] truncate" title={r.message}>{r.message}</span> },
            ]}
          />
          {d && d.rows.length >= limit && (
            <div className="mt-3 flex justify-center">
              <Button size="sm" onClick={() => setLimit((l) => Math.min(2000, l + 200))}>
                Load older entries
              </Button>
            </div>
          )}
        </>
      )}
      <Dialog open={!!open} onClose={() => setOpen(null)} title={open ? `${open.level} · ${open.source} · ${fmtDateTime(open.ts, true)}` : ""} width={720}>
        {open && (
          <div className="space-y-2 text-xs">
            <p className="whitespace-pre-wrap break-words text-text">{open.message}</p>
            {open.context && <pre className="max-h-[50vh] overflow-auto rounded-md border border-border bg-surface-2/50 p-2 text-[11px] text-muted">{JSON.stringify(open.context, null, 2)}</pre>}
          </div>
        )}
      </Dialog>
    </Card>
  );
}

function fmtDetail(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.length ? v.map(fmtDetail).join(", ") : "none";
  if (typeof v === "object") return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k.replace(/_/g, " ")} ${fmtDetail(x)}`).join(" · ");
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

function statusTone(s: string): Tone {
  return s === "ok" ? "up" : s === "degraded" ? "warn" : s === "disabled" ? "neutral" : "down";
}

function DiagnosticsView() {
  const q = useDiagnostics();
  if (q.isError && !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  if (!d) return <SkeletonRows rows={10} />;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Health" icon={<Activity className="size-4" />} subtitle={`Overall ${d.health.status} · up ${fmtAgo(d.health.uptime_s).replace(" ago", "")}`}>
          <div className="space-y-2 text-xs">
            {Object.entries(d.health.components).map(([name, c]) => (
              <div key={name} className="rounded-md border border-border p-2">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{titleCase(name)}</span>
                  <Badge tone={statusTone(c.status)}>{c.status}</Badge>
                </div>
                <div className="mt-1 space-y-0.5 break-words text-muted">
                  {Object.entries(c)
                    .filter(([k]) => k !== "status")
                    .map(([k, v]) => (
                      <div key={k}>
                        <span className="text-faint">{titleCase(k)}:</span> {fmtDetail(v)}
                      </div>
                    ))}
                </div>
              </div>
            ))}
            {d.health.critical_error && (
              <div className="rounded-md border border-down/40 bg-down/5 p-2 text-down">
                Critical: {d.health.critical_error.message} ({d.health.critical_error.source}, {fmtDateTime(d.health.critical_error.ts)})
              </div>
            )}
          </div>
        </Card>
        <Card title="Databases" icon={<Database className="size-4" />} subtitle="SQLite, write-ahead logging">
          <div className="space-y-2 text-xs">
            {Object.entries(d.databases).map(([name, db]) => (
              <div key={name} className="rounded-md border border-border p-2">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{name === "state" ? "State (trades, sessions, experiments)" : "Market data (candles)"}</span>
                  <Badge tone={db.integrity === "ok" ? "up" : "down"}>integrity {db.integrity}</Badge>
                </div>
                <div className="mt-1 break-all text-muted">{db.path}</div>
                <div className="text-muted">
                  {fmtCompact(db.bytes)}B · schema v{db.schema}
                </div>
              </div>
            ))}
            <div className="rounded-md border border-border p-2 text-muted">
              {fmtNum(d.data.candles, 0)} candles in {d.data.series} series
              {d.data.gappy_series.length > 0 && <div className="mt-1 text-warn">Gaps (reported, never filled): {d.data.gappy_series.join("; ")}</div>}
            </div>
          </div>
        </Card>
        <Card title="Versions and safety" icon={<ShieldCheck className="size-4" />}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted">Software</dt>
            <dd className="break-all">{d.versions.software}</dd>
            <dt className="text-muted">Python</dt>
            <dd>{d.versions.python}</dd>
            <dt className="text-muted">Platform</dt>
            <dd className="break-all">{d.versions.platform}</dd>
            <dt className="text-muted">Config hash</dt>
            <dd className="break-all">{d.config_hash}</dd>
            {Object.entries(d.safety).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{titleCase(k)}</dt>
                <dd className="text-up">{v}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Binance connectivity" icon={<HardDrive className="size-4" />}>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-muted">REST</span>
              <Badge tone={d.market.rest.reachable ? "up" : d.market.rest.reachable === false ? "down" : "neutral"}>{d.market.rest.reachable ? "reachable" : d.market.rest.reachable === false ? "unreachable" : "not checked"}</Badge>
            </div>
            {d.market.rest.last_error && <p className="break-words text-down/90">{d.market.rest.last_error}</p>}
            <div className="flex justify-between text-muted">
              <span>Last success</span>
              <span>{fmtDateTime(d.market.rest.last_success_ms)}</span>
            </div>
            <div className="flex justify-between text-muted">
              <span>Latency</span>
              <span>{d.market.rest.latency_ms === null ? "—" : `${fmtNum(d.market.rest.latency_ms, 0)} ms`}</span>
            </div>
            <div className="flex justify-between pt-1">
              <span className="text-muted">WebSocket</span>
              <Badge tone={d.market.websocket.connected ? "up" : "down"}>{d.market.websocket.connected ? "connected" : d.market.websocket.enabled ? "not connected" : "disabled"}</Badge>
            </div>
            {d.market.websocket.last_error && <p className="break-words text-down/90">{d.market.websocket.last_error}</p>}
            <div className="flex justify-between text-muted">
              <span>Messages / reconnects</span>
              <span>
                {d.market.websocket.messages} / {d.market.websocket.reconnects}
              </span>
            </div>
          </div>
        </Card>
        <Card title="Research records">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <Stat label="Experiments" v={d.research.experiments} />
            <Stat label="Failed experiments" v={d.research.failed_experiments} />
            <Stat label="Datasets" v={d.research.datasets} />
            <Stat label="Test-set accesses" v={d.research.test_set_accesses} />
          </div>
          <div className="mt-3 text-xs text-muted">
            Research assistant: {d.research.assistant.available ? <span className="text-up">available</span> : `not available (${d.research.assistant.problems.join("; ")})`}
          </div>
        </Card>
        <Card title="Paper engine">
          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-muted">Status</span>
              <EngineBadge status={d.paper.status} />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted">Data mode</span>
              <span>{d.paper.data_mode ?? "—"}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted">Kill switch</span>
              {d.paper.kill_switch ? <Badge tone="down">ON</Badge> : <Badge>off</Badge>}
            </div>
          </div>
        </Card>
      </div>
      <Card title="Background jobs" subtitle="Downloads, backtests, validations and assistant requests (this run of the server)">
        <DataTable
          rows={d.jobs}
          rowKey={(j) => j.id}
          dense
          empty={<EmptyState title="No jobs since the server started" />}
          columns={[
            { key: "created", header: "Created", render: (j) => fmtDateTime(j.created_at) },
            { key: "kind", header: "Kind", render: (j) => j.kind },
            { key: "label", header: "Job", render: (j) => j.label },
            { key: "status", header: "Status", render: (j) => <Badge tone={j.status === "completed" ? "up" : j.status === "failed" ? "down" : "warn"}>{j.status}</Badge> },
            { key: "progress", header: "Progress", align: "right", render: (j) => `${Math.round(j.progress * 100)}%` },
            { key: "msg", header: "Message", render: (j) => <span className="block max-w-[420px] truncate text-muted" title={j.error ?? j.message}>{j.error ?? j.message}</span> },
          ]}
        />
      </Card>
    </div>
  );
}

function Stat({ label, v }: { label: string; v: number }) {
  return (
    <div className="rounded-md border border-border p-2">
      <div className="text-muted">{label}</div>
      <div className="num text-base font-semibold">{v}</div>
    </div>
  );
}
