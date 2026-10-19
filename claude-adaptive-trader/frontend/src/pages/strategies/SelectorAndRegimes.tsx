// The adaptive selector's view of each pair and the regime detector's reasoning.
import { RegimeBadge, REGIME_COLORS, regimeLabel } from "../../components/domain/Badges";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { EmptyState, ErrorState, Notice, SkeletonRows } from "../../components/ui/Feedback";
import { DataTable } from "../../components/ui/Table";
import { fmtDateTime, fmtNum, titleCase } from "../../lib/format";
import { useRegimes, useSelector } from "../../lib/queries";

export function SelectorView() {
  const q = useSelector();
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data) return <SkeletonRows rows={8} />;
  const d = q.data;
  const approved = Object.entries(d.approved);
  return (
    <div className="space-y-4">
      <Notice>{d.note}</Notice>
      <Card title="Approved versions" subtitle="Only these can be selected for paper trading">
        {approved.length ? (
          <div className="flex flex-wrap gap-2">
            {approved.map(([name, version]) => (
              <Badge key={name} tone="up" title={version}>
                {titleCase(name)} · {version.split("+").pop()}
              </Badge>
            ))}
          </div>
        ) : (
          <EmptyState title="No approved versions" compact>
            Until a person approves a version, the adaptive selector abstains on every candle. Forward tests of single strategies remain possible on the Paper Trading page.
          </EmptyState>
        )}
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        {Object.entries(d.pairs).map(([pair, p]) => (
          <Card key={pair} title={<span className="flex items-center gap-2">{pair} <RegimeBadge regime={p.regime} /></span>} subtitle={p.state ? `Active: ${p.state.active_strategy ? titleCase(p.state.active_strategy) : "none (no trade)"}${p.state.challenger ? ` · challenger ${titleCase(p.state.challenger)} (${p.state.challenger_streak}/${String(d.config.hysteresis_evaluations ?? "")})` : ""} · updated ${fmtDateTime(p.state.updated_ts)}` : "No paper session state for this pair"}>
            {p.regime === "uncertain" ? (
              <EmptyState title="Regime uncertain: no trade" compact>The selector never trades an uncertain regime.</EmptyState>
            ) : (
              <DataTable
                rows={p.table}
                rowKey={(r) => r.strategy}
                dense
                empty={<EmptyState title="No approved strategies to rank" compact />}
                columns={[
                  { key: "s", header: "Strategy", render: (r) => <span className="font-medium">{titleCase(r.strategy)}</span> },
                  { key: "e", header: "Eligible", render: (r) => <Badge tone={r.eligible ? "up" : "neutral"}>{r.eligible ? "yes" : "no"}</Badge> },
                  { key: "sc", header: "Score", align: "right", render: (r) => fmtNum(r.score, 3) },
                  { key: "why", header: "Why not", render: (r) => <span className="block max-w-[380px] whitespace-normal text-muted">{r.reasons.join("; ") || "—"}</span> },
                ]}
              />
            )}
          </Card>
        ))}
      </div>
      <Card title="Selector settings" subtitle="Edit them on the Settings page">
        <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-3 xl:grid-cols-4">
          {Object.entries(d.config).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-2 border-b border-border/50 py-1">
              <span className="text-muted">{titleCase(k)}</span>
              <span className="num">{String(v)}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

const FEATURE_LABEL: Record<string, string> = {
  close: "Close",
  ema_fast: "EMA 20",
  ema_mid: "EMA 50",
  ema_slow: "EMA 200",
  adx: "ADX",
  plus_di: "+DI",
  minus_di: "−DI",
  atr_pct: "ATR % of price",
  atr_rank: "ATR percentile",
  bb_width_rank: "Bollinger width percentile",
  slope_mid_atr: "EMA 50 slope (ATR)",
  range_atr: "Candle range (ATR)",
  rel_volume: "Relative volume",
  rsi: "RSI 14",
};

export function RegimesView() {
  const q = useRegimes();
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data) return <SkeletonRows rows={8} />;
  const d = q.data;
  return (
    <div className="space-y-4">
      <Notice>
        Regimes are computed from closed {d.timeframe} candles with fixed, documented rules (trend, range, volatility breakout, uncertain). When the rules disagree or
        confidence is low the regime is <b>uncertain</b> and nothing trades.
      </Notice>
      <div className="grid gap-4 xl:grid-cols-2">
        {Object.entries(d.pairs).map(([pair, r]) => (
          <Card key={pair} title={<span className="flex items-center gap-2">{pair} {r.available ? <RegimeBadge regime={r.regime} confidence={r.confidence} /> : <Badge>no data</Badge>}</span>} subtitle={r.available ? `Candle ${fmtDateTime(r.candle_ts)}${r.stale ? " · stale (no recent candles stored)" : ""}` : r.reason}>
            {r.available ? (
              <div className="space-y-3 text-xs">
                <p className="leading-5 text-text">{r.reason}</p>
                {r.history && (
                  <div>
                    <div className="mb-1 text-muted">Last {r.history.length} candles</div>
                    <div className="flex h-5 overflow-hidden rounded">
                      {r.history.map((h) => (
                        <div key={h.ts} className="h-full flex-1" style={{ background: REGIME_COLORS[h.regime] ?? "#5d687a" }} title={`${fmtDateTime(h.ts)}: ${regimeLabel(h.regime)}`} />
                      ))}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-faint">
                      {Object.entries(REGIME_COLORS).map(([k, c]) => (
                        <span key={k} className="inline-flex items-center gap-1">
                          <span className="size-2 rounded-sm" style={{ background: c }} /> {regimeLabel(k)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {r.explain && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <div className="mb-1 font-medium">Features</div>
                      {Object.entries(r.explain.features).map(([k, v]) => (
                        <div key={k} className="flex justify-between border-b border-border/50 py-0.5">
                          <span className="text-muted">{FEATURE_LABEL[k] ?? k.replace("htf_", "Trend ")}</span>
                          <span className="num">{v === null ? "—" : k.startsWith("htf_") ? (v > 0 ? "up" : v < 0 ? "down" : "flat") : fmtNum(v, Math.abs(v) >= 100 ? 2 : 3)}</span>
                        </div>
                      ))}
                    </div>
                    <div>
                      <div className="mb-1 font-medium">Thresholds</div>
                      {Object.entries(r.explain.thresholds).map(([k, v]) => (
                        <div key={k} className="flex justify-between border-b border-border/50 py-0.5">
                          <span className="text-muted">{titleCase(k)}</span>
                          <span className="num">{v}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <EmptyState title="No candles stored for this pair" compact>Download history on the Market page.</EmptyState>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
