// Recharts building blocks for results: equity (with optional benchmark), drawdown, bars,
// histograms and a monthly-returns heatmap. Every chart is drawn from stored results only.
import { useId, useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { cn } from "../../lib/cn";
import { fmtDateTime, fmtNum, fmtPct, fmtUsd } from "../../lib/format";
import type { EquityPoint, HistBin } from "../../lib/types";
import { EmptyState } from "../ui/Feedback";
import { CC, axisProps, shortDate, tooltipStyle } from "./theme";

const yearOf = (ms: number) => new Date(ms).getUTCFullYear();

function timeTick(points: { ts: number }[]) {
  const span = points.length ? points[points.length - 1].ts - points[0].ts : 0;
  return (v: number) => (span > 400 * 86_400_000 ? `${shortDate(v)} ${String(yearOf(v)).slice(2)}` : shortDate(v));
}

export function EquityChart({ points, height = 260, initial, showBenchmark = true, empty }: {
  points: EquityPoint[] | undefined;
  height?: number;
  initial?: number;
  showBenchmark?: boolean;
  empty?: string;
}) {
  const id = useId().replace(/:/g, "");
  const data = useMemo(() => (points ?? []).map((p) => ({ ts: p.ts, equity: p.equity, benchmark: p.benchmark ?? null })), [points]);
  if (!data.length) return <EmptyState title={empty ?? "No equity recorded yet"} compact />;
  const hasBench = showBenchmark && data.some((d) => d.benchmark !== null);
  const last = data[data.length - 1].equity;
  const base = initial ?? data[0].equity;
  const color = last >= base ? CC.up : CC.down;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={`eq-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={CC.grid} vertical={false} />
        <XAxis dataKey="ts" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={timeTick(data)} minTickGap={40} {...axisProps} />
        <YAxis domain={["auto", "auto"]} width={72} tickFormatter={(v: number) => fmtUsd(v, 0)} {...axisProps} />
        <Tooltip
          {...tooltipStyle}
          labelFormatter={(l) => fmtDateTime(Number(l))}
          formatter={(v, name) => [fmtUsd(Number(v)), name === "benchmark" ? "Buy & hold" : "Strategy equity"]}
        />
        {initial !== undefined && <ReferenceLine y={initial} stroke={CC.faint} strokeDasharray="4 4" />}
        <Area type="monotone" dataKey="equity" stroke={color} strokeWidth={1.6} fill={`url(#eq-${id})`} isAnimationActive={false} dot={false} />
        {hasBench && <Line type="monotone" dataKey="benchmark" stroke={CC.benchmark} strokeDasharray="5 4" strokeWidth={1.2} dot={false} isAnimationActive={false} />}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function DrawdownChart({ points, height = 160 }: { points: { ts: number; drawdown_pct: number }[] | undefined; height?: number }) {
  const id = useId().replace(/:/g, "");
  const data = useMemo(() => (points ?? []).map((p) => ({ ts: p.ts, dd: -Math.abs(p.drawdown_pct) })), [points]);
  if (!data.length) return <EmptyState title="No drawdown data" compact />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={`dd-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CC.down} stopOpacity={0.05} />
            <stop offset="100%" stopColor={CC.down} stopOpacity={0.35} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={CC.grid} vertical={false} />
        <XAxis dataKey="ts" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={timeTick(data)} minTickGap={40} {...axisProps} />
        <YAxis width={72} tickFormatter={(v: number) => `${v.toFixed(1)}%`} {...axisProps} />
        <Tooltip {...tooltipStyle} labelFormatter={(l) => fmtDateTime(Number(l))} formatter={(v) => [fmtPct(Number(v), 2), "Drawdown"]} />
        <Area type="monotone" dataKey="dd" stroke={CC.down} strokeWidth={1.2} fill={`url(#dd-${id})`} isAnimationActive={false} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function SeriesArea({ points, dataKey, label, height = 140, unit = "%" }: {
  points: Record<string, number>[] | undefined;
  dataKey: string;
  label: string;
  height?: number;
  unit?: string;
}) {
  const data = points ?? [];
  if (!data.length) return <EmptyState title={`No ${label.toLowerCase()} data`} compact />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={CC.grid} vertical={false} />
        <XAxis dataKey="ts" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={timeTick(data as { ts: number }[])} minTickGap={40} {...axisProps} />
        <YAxis width={72} tickFormatter={(v: number) => `${fmtNum(v, 0)}${unit}`} {...axisProps} />
        <Tooltip {...tooltipStyle} labelFormatter={(l) => fmtDateTime(Number(l))} formatter={(v) => [`${fmtNum(Number(v), 2)}${unit}`, label]} />
        <Area type="stepAfter" dataKey={dataKey} stroke={CC.info} strokeWidth={1.2} fill="rgba(78,168,255,0.15)" isAnimationActive={false} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function SignedBars({ data, xKey, yKey, label, height = 180, money = true }: {
  data: Record<string, string | number>[] | undefined;
  xKey: string;
  yKey: string;
  label: string;
  height?: number;
  money?: boolean;
}) {
  const rows = data ?? [];
  if (!rows.length) return <EmptyState title={`No ${label.toLowerCase()} yet`} compact />;
  const fmt = (v: number) => (money ? fmtUsd(v, Math.abs(v) >= 100 ? 0 : 2) : fmtNum(v, 2));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={CC.grid} vertical={false} />
        <XAxis dataKey={xKey} minTickGap={24} {...axisProps} />
        <YAxis width={72} tickFormatter={fmt} {...axisProps} />
        <ReferenceLine y={0} stroke={CC.faint} />
        <Tooltip {...tooltipStyle} cursor={{ fill: "rgba(124,140,255,0.08)" }} formatter={(v) => [fmt(Number(v)), label]} />
        <Bar dataKey={yKey} isAnimationActive={false} radius={[2, 2, 0, 0]}>
          {rows.map((r, i) => (
            <Cell key={i} fill={Number(r[yKey]) >= 0 ? CC.up : CC.down} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Histogram({ bins, label, height = 180, unit = "", zeroSplit = true }: {
  bins: HistBin[] | undefined;
  label: string;
  height?: number;
  unit?: string;
  zeroSplit?: boolean;
}) {
  const data = (bins ?? []).map((b) => ({ mid: (b.from + b.to) / 2, label: `${fmtNum(b.from, 2)} to ${fmtNum(b.to, 2)}${unit}`, count: b.count }));
  if (!data.length) return <EmptyState title={`No ${label.toLowerCase()} yet`} compact />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }} barCategoryGap={1}>
        <CartesianGrid stroke={CC.grid} vertical={false} />
        <XAxis dataKey="mid" tickFormatter={(v: number) => `${fmtNum(v, Math.abs(v) >= 100 ? 0 : 1)}${unit}`} minTickGap={20} {...axisProps} />
        <YAxis width={40} allowDecimals={false} {...axisProps} />
        <Tooltip {...tooltipStyle} cursor={{ fill: "rgba(124,140,255,0.08)" }} labelFormatter={(_l, p) => String(p?.[0]?.payload?.label ?? "")} formatter={(v) => [String(v), "Trades"]} />
        <Bar dataKey="count" isAnimationActive={false}>
          {data.map((d, i) => (
            <Cell key={i} fill={!zeroSplit ? CC.accent : d.mid >= 0 ? CC.up : CC.down} fillOpacity={0.85} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function MonthlyHeatmap({ returns }: { returns: Record<string, number | null> | undefined }) {
  const years = useMemo(() => {
    const by: Record<string, Record<number, number | null>> = {};
    for (const [k, v] of Object.entries(returns ?? {})) {
      const [y, m] = k.split("-");
      (by[y] ??= {})[Number(m) - 1] = v;
    }
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [returns]);
  if (!years.length) return <EmptyState title="No monthly returns yet" compact />;
  const max = Math.max(1, ...Object.values(returns ?? {}).map((v) => Math.abs(v ?? 0)));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-separate border-spacing-1 text-center text-[11px]">
        <thead>
          <tr>
            <th className="w-12 text-left font-medium text-muted">Year</th>
            {MONTHS.map((m) => (
              <th key={m} className="font-medium text-muted">{m}</th>
            ))}
            <th className="font-medium text-muted">Year</th>
          </tr>
        </thead>
        <tbody>
          {years.map(([y, months]) => {
            const compounded = Object.values(months).reduce<number>((acc, v) => acc * (1 + (v ?? 0) / 100), 1);
            return (
              <tr key={y}>
                <td className="text-left font-medium text-muted">{y}</td>
                {MONTHS.map((_, i) => {
                  const v = months[i];
                  const has = v !== undefined && v !== null;
                  const alpha = has ? Math.min(0.85, 0.12 + (Math.abs(v) / max) * 0.7) : 0;
                  return (
                    <td
                      key={i}
                      title={has ? `${y}-${String(i + 1).padStart(2, "0")}: ${fmtPct(v, 2, true)}` : "no data"}
                      className={cn("num h-8 rounded", !has && "bg-surface-2/50 text-faint")}
                      style={has ? { background: v >= 0 ? `rgba(46,189,133,${alpha})` : `rgba(246,70,93,${alpha})` } : undefined}
                    >
                      {has ? fmtPct(v, 1) : "·"}
                    </td>
                  );
                })}
                <td className={cn("num font-semibold", compounded >= 1 ? "text-up" : "text-down")}>{fmtPct((compounded - 1) * 100, 1, true)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
