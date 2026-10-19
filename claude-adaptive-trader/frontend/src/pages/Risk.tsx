// Risk management: configured limits against the hard caps compiled into the backend, current
// utilisation, circuit breakers, the kill switch and every recent pre-trade decision.
import { useState } from "react";
import { CircleAlert, Lock, RotateCcw, ShieldAlert, ShieldCheck } from "lucide-react";
import { RegimeBadge } from "../components/domain/Badges";
import { CHECK_HELP } from "../components/domain/Checks";
import { ENGINE_KEYS, KillSwitchButton } from "../components/domain/Controls";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog, Dialog } from "../components/ui/Dialog";
import { EmptyState, ErrorState, Notice, SkeletonRows } from "../components/ui/Feedback";
import { Meter, Segmented } from "../components/ui/Form";
import { DataTable } from "../components/ui/Table";
import { post } from "../lib/api";
import { fmtDateTime, fmtNum, fmtPct, fmtUsd, titleCase } from "../lib/format";
import { useRiskDecisions, useRiskStatus } from "../lib/queries";
import type { Decision, RiskStatus } from "../lib/types";
import { useAction } from "../lib/useAction";
import { useLiveRefresh } from "../lib/useLiveRefresh";
import { DecisionDetail } from "./Paper";

export function RiskPage() {
  const q = useRiskStatus();
  useLiveRefresh("paper", [["risk-status"], ["risk-decisions"]], 3000);
  if (q.isError && !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const r = q.data;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-3">
        <KillSwitchCard r={r} />
        <BreakersCard r={r} />
        <UtilisationCard r={r} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
        <LimitsCard r={r} />
        <ChecksCard r={r} />
      </div>
      <RecentDecisions />
      {r && (
        <Notice tone="warn" icon={<CircleAlert className="size-3.5" />}>
          {r.stop_note}
        </Notice>
      )}
    </div>
  );
}

function KillSwitchCard({ r }: { r: RiskStatus | undefined }) {
  const ks = r?.kill_switch;
  return (
    <Card title="Kill switch" subtitle="Immediate stop for every new entry" icon={<ShieldAlert className="size-4" />}>
      {!r ? (
        <SkeletonRows rows={3} />
      ) : (
        <div className="space-y-3">
          <div className={`rounded-lg border p-3 ${ks?.on ? "border-down/40 bg-down/10" : "border-up/30 bg-up/5"}`}>
            <div className="flex items-center gap-2 text-sm font-semibold">
              {ks?.on ? <ShieldAlert className="size-4 text-down" /> : <ShieldCheck className="size-4 text-up" />}
              {ks?.on ? "ON: new entries are blocked" : "Off: entries allowed by the risk checks"}
            </div>
            {ks?.on && (
              <p className="mt-1 text-xs text-muted">
                Set by {ks.by ?? "unknown"} {ks.at ? `at ${fmtDateTime(ks.at)}` : ""} · {ks.reason}
              </p>
            )}
          </div>
          <KillSwitchButton state={ks} />
          <p className="text-[11px] leading-4 text-faint">The switch persists across restarts. Open positions keep their stops and exits; nothing is sold automatically.</p>
        </div>
      )}
    </Card>
  );
}

function BreakersCard({ r }: { r: RiskStatus | undefined }) {
  const [open, setOpen] = useState(false);
  const reset = useAction((note: string) => post("/api/risk/breakers/reset-drawdown", { note }), {
    success: "Drawdown breaker reset; a new peak starts now",
    invalidate: ENGINE_KEYS,
    onSuccess: () => setOpen(false),
  });
  const b = r?.breakers;
  const u = r?.utilization;
  return (
    <Card title="Circuit breakers" subtitle="Automatic pauses of new entries">
      {!r || !u ? (
        <SkeletonRows rows={3} />
      ) : (
        <div className="space-y-4 text-xs">
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="font-medium">Daily loss limit</span>
              {b?.daily_tripped ? <Badge tone="down">tripped</Badge> : <Badge tone="up">armed</Badge>}
            </div>
            <Meter value={u.daily_loss_pct} max={u.daily_loss_limit_pct} tone="warn" detail={`${fmtPct(u.daily_loss_pct, 2)} of ${fmtPct(u.daily_loss_limit_pct, 2)}`} label="Loss since the UTC day started" />
            <p className="mt-1 text-faint">{b?.daily_tripped ? `Tripped ${fmtDateTime(b.daily_tripped_at)}; clears at the next UTC day.` : "Stops new entries for the rest of the UTC day when reached."}</p>
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="font-medium">Maximum drawdown</span>
              {b?.drawdown_tripped ? <Badge tone="down">tripped</Badge> : <Badge tone="up">armed</Badge>}
            </div>
            <Meter value={u.drawdown_pct} max={u.drawdown_limit_pct} tone="warn" detail={`${fmtPct(u.drawdown_pct, 2)} of ${fmtPct(u.drawdown_limit_pct, 2)}`} label={b?.peak_equity ? `Below the peak of ${fmtUsd(b.peak_equity)}` : "Below the peak (no session yet)"} />
            <p className="mt-1 text-faint">{b?.drawdown_tripped ? `Tripped ${fmtDateTime(b.drawdown_tripped_at)}; only a person can reset it.` : "Stops new entries until a person resets it."}</p>
          </div>
          <Button size="sm" icon={<RotateCcw className="size-3.5" />} disabled={!r.session} onClick={() => setOpen(true)}>
            Reset drawdown breaker
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={open}
        onClose={() => setOpen(false)}
        title="Reset the drawdown breaker?"
        confirmLabel="Reset"
        danger
        busy={reset.isPending}
        requireNote="Why is it safe to resume? (recorded)"
        onConfirm={(note) => reset.mutate(note)}
      >
        <p>The current equity becomes the new peak and new entries are allowed again, subject to every other check. The reset and your note are logged.</p>
      </ConfirmDialog>
    </Card>
  );
}

function UtilisationCard({ r }: { r: RiskStatus | undefined }) {
  const u = r?.utilization;
  return (
    <Card title="Utilisation" subtitle={u ? `Equity ${fmtUsd(u.equity)} (simulated)` : undefined}>
      {!u ? (
        <SkeletonRows rows={4} />
      ) : (
        <div className="space-y-3.5">
          <Meter label="Open risk" value={u.open_risk_pct} max={u.open_risk_limit_pct} detail={`${fmtUsd(u.open_risk)} · ${fmtPct(u.open_risk_pct, 2)} / ${fmtPct(u.open_risk_limit_pct, 2)}`} />
          <Meter label="Total exposure" value={u.exposure_pct} max={u.exposure_limit_pct} detail={`${fmtPct(u.exposure_pct, 1)} / ${fmtPct(u.exposure_limit_pct, 0)}`} />
          <Meter label="Open positions" value={u.positions} max={u.positions_limit} detail={`${u.positions} / ${u.positions_limit}`} />
          <Meter label="Daily loss" value={u.daily_loss_pct} max={u.daily_loss_limit_pct} tone="warn" detail={`${fmtPct(u.daily_loss_pct, 2)} / ${fmtPct(u.daily_loss_limit_pct, 2)}`} />
          <Meter label="Drawdown" value={u.drawdown_pct} max={u.drawdown_limit_pct} tone="warn" detail={`${fmtPct(u.drawdown_pct, 2)} / ${fmtPct(u.drawdown_limit_pct, 2)}`} />
          {r?.engine && (
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted">Execution engine</span>
              {r.engine.healthy ? <Badge tone="up">healthy</Badge> : <Badge tone="warn" title={r.engine.problems.join("; ")}>degraded</Badge>}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

const LIMIT_ROWS: { key: string; label: string; unit: "%" | "$" | "n" | "x"; hard?: string }[] = [
  { key: "risk_per_trade_pct", label: "Risk per trade", unit: "%", hard: "risk_per_trade_pct" },
  { key: "max_risk_per_trade_pct", label: "Maximum risk per trade", unit: "%", hard: "risk_per_trade_pct" },
  { key: "max_open_risk_pct", label: "Total open risk", unit: "%", hard: "open_risk_pct" },
  { key: "max_position_pct", label: "Largest single position", unit: "%", hard: "position_pct" },
  { key: "max_total_exposure_pct", label: "Total exposure", unit: "%", hard: "total_exposure_pct" },
  { key: "max_positions", label: "Open positions", unit: "n", hard: "positions" },
  { key: "max_correlated_positions", label: "Positions in one correlation group", unit: "n", hard: "positions" },
  { key: "daily_loss_limit_pct", label: "Daily loss limit", unit: "%", hard: "daily_loss_pct" },
  { key: "max_drawdown_pct", label: "Maximum drawdown", unit: "%", hard: "drawdown_pct" },
  { key: "min_order_notional", label: "Minimum order value", unit: "$" },
  { key: "min_stop_distance_pct", label: "Minimum stop distance", unit: "%" },
  { key: "max_stop_distance_pct", label: "Maximum stop distance", unit: "%" },
  { key: "max_cost_to_reward", label: "Costs as share of planned reward", unit: "x" },
  { key: "max_spread_pct", label: "Maximum spread", unit: "%" },
  { key: "liquidity_max_volume_pct", label: "Order size vs candle volume", unit: "%" },
];

function fmtLimit(v: unknown, unit: string): string {
  if (typeof v !== "number") return v === undefined ? "—" : String(v);
  if (unit === "%") return fmtPct(v, 2);
  if (unit === "$") return fmtUsd(v, 0);
  if (unit === "x") return fmtNum(v, 2);
  return String(v);
}

function LimitsCard({ r }: { r: RiskStatus | undefined }) {
  const rows = LIMIT_ROWS.filter((row) => r && row.key in r.limits);
  return (
    <Card title="Limits" subtitle="Configured in Settings, never above the hard caps compiled into the backend" icon={<Lock className="size-4" />}>
      {!r ? (
        <SkeletonRows />
      ) : (
        <>
          <DataTable
            rows={rows}
            rowKey={(row) => row.key}
            dense
            columns={[
              { key: "label", header: "Limit", render: (row) => row.label },
              { key: "value", header: "Configured", align: "right", render: (row) => <span className="font-medium">{fmtLimit(r.limits[row.key], row.unit)}</span> },
              { key: "hard", header: "Hard cap", align: "right", render: (row) => (row.hard ? fmtLimit(r.hard_limits[row.hard], row.unit) : <span className="text-faint">—</span>) },
            ]}
          />
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <Badge tone="neutral">Leverage: not available</Badge>
            <Badge tone="neutral">Short selling: not available</Badge>
            <Badge tone="neutral">Real-money orders: not implemented</Badge>
            {Array.isArray(r.limits.correlation_groups) && (
              <Badge tone="info">Correlation groups: {(r.limits.correlation_groups as string[][]).map((g) => g.join("/")).join(", ")}</Badge>
            )}
          </div>
          <p className="mt-2 text-[11px] text-faint">
            Position size = risk budget ÷ stop distance, then reduced to the smallest of the cash, liquidity, exposure and exchange limits.
          </p>
        </>
      )}
    </Card>
  );
}

function ChecksCard({ r }: { r: RiskStatus | undefined }) {
  return (
    <Card title="Fourteen mandatory pre-trade checks" subtitle="Every entry passes all of them or is rejected, independently of any AI component">
      {!r ? (
        <SkeletonRows />
      ) : (
        <ol className="grid gap-1.5 sm:grid-cols-2">
          {r.checks.map((c, i) => (
            <li key={c} className="rounded-md border border-border bg-surface-2/40 px-2.5 py-1.5 text-xs">
              <div className="font-medium text-text">
                <span className="num mr-1 text-faint">{i + 1}.</span>
                {titleCase(c)}
              </div>
              <div className="text-muted">{CHECK_HELP[c] ?? ""}</div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function RecentDecisions() {
  const [mode, setMode] = useState<"all" | "rejected">("all");
  const q = useRiskDecisions(mode === "rejected");
  const [open, setOpen] = useState<Decision | null>(null);
  return (
    <Card title="Recent risk decisions" subtitle="Entry signals of the current session with the outcome of each check" actions={<Segmented size="sm" value={mode} onChange={setMode} options={[{ value: "all", label: "All" }, { value: "rejected", label: "Rejected only" }]} />}>
      <DataTable
        rows={q.data}
        loading={q.isLoading}
        rowKey={(d) => d.id}
        dense
        maxHeight={420}
        onRowClick={setOpen}
        empty={<EmptyState title="No entry signals evaluated yet">Signals appear here as soon as a paper session produces them.</EmptyState>}
        columns={[
          { key: "ts", header: "Candle close", render: (d) => fmtDateTime(d.ts) },
          { key: "pair", header: "Pair", render: (d) => d.pair },
          { key: "strategy", header: "Strategy", render: (d) => (d.strategy ? titleCase(d.strategy) : "—") },
          { key: "regime", header: "Regime", render: (d) => <RegimeBadge regime={d.regime} /> },
          { key: "outcome", header: "Outcome", render: (d) => <Badge tone={d.accepted ? "up" : "down"}>{d.accepted ? "approved" : "rejected"}</Badge> },
          {
            key: "checks",
            header: "Checks passed",
            align: "right",
            render: (d) => (d.checks ? `${d.checks.filter((c) => c.passed).length}/${d.checks.length}` : "—"),
          },
          { key: "size", header: "Size", align: "right", render: (d) => (d.signal?.sizing ? fmtUsd(d.signal.sizing.notional) : "—") },
          { key: "reason", header: "Reason", render: (d) => <span className="block max-w-[420px] truncate text-muted" title={d.reason}>{d.reason}</span> },
        ]}
      />
      <Dialog open={!!open} onClose={() => setOpen(null)} title={open ? `${open.pair} · ${open.accepted ? "approved" : "rejected"} · ${fmtDateTime(open.ts)}` : ""} width={760}>
        {open && <DecisionDetail d={open} />}
      </Dialog>
    </Card>
  );
}
