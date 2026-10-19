// Tables shared by several pages: completed trades, grouped statistics and metric summaries.
import { useState } from "react";
import { cn } from "../../lib/cn";
import { fmtDateTime, fmtNum, fmtPct, fmtPrice, fmtSignedUsd, fmtUsd, titleCase, toneOf } from "../../lib/format";
import type { GroupStats, Metrics, TradeRow } from "../../lib/types";
import { Dialog } from "../ui/Dialog";
import { EmptyState } from "../ui/Feedback";
import { DataTable, type Column } from "../ui/Table";
import { RegimeBadge, regimeLabel } from "./Badges";

const tone = (v: number | null | undefined) => {
  const t = toneOf(v);
  return t === "up" ? "text-up" : t === "down" ? "text-down" : "";
};

export function TradesTable({ trades, loading, maxHeight = 520, showRun, empty, compact }: {
  trades: TradeRow[] | undefined;
  loading?: boolean;
  maxHeight?: number;
  showRun?: boolean;
  empty?: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState<TradeRow | null>(null);
  const columns: Column<TradeRow>[] = [
    { key: "exit_ts", header: "Closed", render: (t) => fmtDateTime(t.exit_ts), sort: (t) => t.exit_ts },
    { key: "pair", header: "Pair", render: (t) => <span className="font-medium">{t.pair}</span>, sort: (t) => t.pair },
    { key: "strategy", header: "Strategy", render: (t) => titleCase(t.strategy), sort: (t) => t.strategy },
    { key: "regime", header: "Regime", render: (t) => <RegimeBadge regime={t.regime} />, sort: (t) => t.regime },
    { key: "entry", header: "Entry", align: "right", render: (t) => fmtPrice(t.entry_price) },
    { key: "exit", header: "Exit", align: "right", render: (t) => fmtPrice(t.exit_price) },
    { key: "net", header: "Net PnL", align: "right", render: (t) => <span className={tone(t.net_pnl)}>{fmtSignedUsd(t.net_pnl)}</span>, sort: (t) => t.net_pnl },
    { key: "ret", header: "Return", align: "right", render: (t) => <span className={tone(t.return_pct)}>{fmtPct(t.return_pct, 2, true)}</span>, sort: (t) => t.return_pct },
    { key: "r", header: "R", align: "right", render: (t) => <span className={tone(t.r_multiple)}>{fmtNum(t.r_multiple, 2)}</span>, sort: (t) => t.r_multiple },
    { key: "fees", header: "Fees", align: "right", render: (t) => fmtUsd(t.fees), sort: (t) => t.fees },
    { key: "exit_reason", header: "Exit reason", render: (t) => <span className="block max-w-[220px] truncate text-muted" title={t.exit_reason}>{t.exit_reason}</span> },
  ];
  if (showRun) columns.splice(1, 0, { key: "run", header: "Run", render: (t) => <span className="text-muted">{t.source} · {t.run_id}</span> });
  const shown = compact ? columns.filter((c) => ["exit_ts", "pair", "net", "ret", "r", "exit_reason"].includes(c.key)) : columns;
  return (
    <>
      <DataTable
        rows={trades}
        columns={shown}
        rowKey={(t) => t.id}
        loading={loading}
        maxHeight={maxHeight}
        dense
        initialSort={{ key: "exit_ts", dir: "desc" }}
        onRowClick={setOpen}
        empty={<EmptyState title={empty ?? "No completed trades"} />}
      />
      <Dialog open={!!open} onClose={() => setOpen(null)} title={open ? `${open.pair} · ${titleCase(open.strategy)} · trade ${open.position_key}` : ""} width={640}>
        {open && <TradeDetail t={open} />}
      </Dialog>
    </>
  );
}

function Row({ k, v, className }: { k: string; v: React.ReactNode; className?: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-border/50 py-1 last:border-0">
      <span className="text-muted">{k}</span>
      <span className={cn("num text-right text-text", className)}>{v}</span>
    </div>
  );
}

function TradeDetail({ t }: { t: TradeRow }) {
  return (
    <div className="space-y-3 text-xs">
      <div className="grid gap-x-6 sm:grid-cols-2">
        <div>
          <Row k="Opened" v={fmtDateTime(t.entry_ts)} />
          <Row k="Closed" v={fmtDateTime(t.exit_ts)} />
          <Row k="Entry price" v={fmtPrice(t.entry_price)} />
          <Row k="Exit price" v={fmtPrice(t.exit_price)} />
          <Row k="Quantity" v={fmtNum(t.qty, 6)} />
          <Row k="Stop / target" v={`${fmtPrice(t.stop_price)} / ${fmtPrice(t.take_profit)}`} />
          <Row k="Candles held" v={t.bars_held ?? "—"} />
        </div>
        <div>
          <Row k="Gross PnL" v={fmtSignedUsd(t.gross_pnl)} className={tone(t.gross_pnl)} />
          <Row k="Fees" v={fmtUsd(t.fees)} />
          <Row k="Slippage (estimate)" v={fmtUsd(t.slippage_cost)} />
          <Row k="Net PnL" v={fmtSignedUsd(t.net_pnl)} className={tone(t.net_pnl)} />
          <Row k="Initial risk" v={fmtUsd(t.initial_risk)} />
          <Row k="R multiple" v={fmtNum(t.r_multiple, 3)} className={tone(t.r_multiple)} />
          <Row k="MAE / MFE" v={`${fmtPct(t.mae_pct, 2)} / ${fmtPct(t.mfe_pct, 2)}`} />
        </div>
      </div>
      <div className="grid gap-x-6 sm:grid-cols-2">
        <div>
          <Row k="Regime at entry" v={regimeLabel(t.regime)} />
          <Row k="Environment" v={t.environment ?? "—"} />
        </div>
        <div>
          <Row k="Version" v={<span className="break-all">{t.strategy_version}</span>} />
          <Row k="Run" v={`${t.source} · ${t.run_id} · ${t.scenario}`} />
        </div>
      </div>
      <div>
        <div className="mb-0.5 font-medium text-text">Why it entered</div>
        <p className="leading-5 text-muted">{t.entry_reason || "—"}</p>
      </div>
      <div>
        <div className="mb-0.5 font-medium text-text">Why it exited</div>
        <p className="leading-5 text-muted">{t.exit_reason}</p>
      </div>
      <p className="text-[11px] text-faint">Simulated trade. Fills include modelled fees, slippage and spread; they are not exchange executions.</p>
    </div>
  );
}

export function GroupTable({ groups, label, format = titleCase }: { groups: GroupStats | undefined | null; label: string; format?: (k: string) => string }) {
  const rows = Object.entries(groups ?? {}).map(([k, v]) => ({ key: k, ...v }));
  if (!rows.length) return <EmptyState title={`No trades to group by ${label.toLowerCase()}`} compact />;
  return (
    <DataTable
      rows={rows}
      rowKey={(r) => r.key}
      dense
      initialSort={{ key: "trades", dir: "desc" }}
      columns={[
        { key: "key", header: label, render: (r) => <span className="font-medium">{format(r.key)}</span>, sort: (r) => r.key },
        { key: "trades", header: "Trades", align: "right", render: (r) => r.trades, sort: (r) => r.trades },
        { key: "win", header: "Win rate", align: "right", render: (r) => fmtPct(r.win_rate_pct ?? null, 1), sort: (r) => r.win_rate_pct ?? null },
        { key: "exp", header: "Expectancy (R)", align: "right", render: (r) => <span className={tone(r.expectancy_r)}>{fmtNum(r.expectancy_r ?? null, 3)}</span>, sort: (r) => r.expectancy_r ?? null },
        { key: "pf", header: "Profit factor", align: "right", render: (r) => fmtNum(r.profit_factor ?? null, 2), sort: (r) => r.profit_factor ?? null },
        { key: "net", header: "Net PnL", align: "right", render: (r) => <span className={tone(r.net_pnl)}>{fmtSignedUsd(r.net_pnl ?? null)}</span>, sort: (r) => r.net_pnl ?? null },
        { key: "fees", header: "Fees", align: "right", render: (r) => fmtUsd(r.fees ?? null) },
      ]}
    />
  );
}

export function MetricsList({ m }: { m: Metrics }) {
  const items: [string, string, number | null | undefined][] = [
    ["Trades", String(m.trades), null],
    ["Net return", fmtPct(m.net_return_pct, 2, true), m.net_return_pct],
    ["Net profit", fmtSignedUsd(m.net_profit), m.net_profit],
    ["Gross return (before fees and slippage)", fmtPct(m.gross_return_pct, 2, true), m.gross_return_pct],
    ["Fees paid", fmtUsd(m.total_fees), null],
    ["Slippage (estimate)", fmtUsd(m.slippage_estimate), null],
    ["Win rate", fmtPct(m.win_rate_pct, 1), null],
    ["Expectancy", `${fmtSignedUsd(m.expectancy)} · ${fmtNum(m.expectancy_r, 3)} R`, m.expectancy_r],
    ["Profit factor", fmtNum(m.profit_factor, 2), null],
    ["Average win / loss", `${fmtUsd(m.avg_win)} / ${fmtUsd(m.avg_loss)}`, null],
    ["Payoff ratio", fmtNum(m.payoff_ratio, 2), null],
    ["Best / worst trade", `${fmtSignedUsd(m.best_trade)} / ${fmtSignedUsd(m.worst_trade)}`, null],
    ["Net without best trade", fmtSignedUsd(m.net_without_best_trade), m.net_without_best_trade],
    ["Longest losing streak", String(m.longest_losing_streak), null],
    ["Max drawdown", fmtPct(m.max_drawdown_pct, 2), null],
    ["Sharpe (daily, annualised)", m.sharpe === null ? "not meaningful" : fmtNum(m.sharpe, 2), null],
    ["Sortino", m.sortino === null ? "not meaningful" : fmtNum(m.sortino, 2), null],
    ["CAGR", m.cagr_pct === null ? "under 30 days" : fmtPct(m.cagr_pct, 2), m.cagr_pct],
    ["Time in market", fmtPct(m.exposure_pct, 1), null],
    ["Average hold (candles)", fmtNum(m.avg_bars_held, 1), null],
    ["Avg MAE / MFE", `${fmtPct(m.avg_mae_pct, 2)} / ${fmtPct(m.avg_mfe_pct, 2)}`, null],
    ["Buy & hold return", fmtPct(m.benchmark_return_pct, 2, true), m.benchmark_return_pct],
    ["Excess over buy & hold", fmtPct(m.excess_return_pct, 2, true), m.excess_return_pct],
  ];
  return (
    <div>
      <div className="grid gap-x-6 text-xs sm:grid-cols-2 xl:grid-cols-3">
        {items.map(([k, v, t]) => (
          <Row key={k} k={k} v={v} className={t === null || t === undefined ? "" : tone(t)} />
        ))}
      </div>
      {m.notes?.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-faint">
          {m.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
