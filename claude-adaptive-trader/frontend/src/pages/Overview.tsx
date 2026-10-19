// Overview: the simulated portfolio, current regimes, risk utilisation, the data feed and the
// engine at a glance. Everything comes from /api/dashboard, which reads stored state only.
import { Link } from "react-router-dom";
import { ArrowRight, CircleAlert, CirclePlay, Radio, ShieldAlert, WifiOff } from "lucide-react";
import { EquityChart } from "../components/charts/Charts";
import { EngineBadge, OrderStatusBadge, RegimeBadge, SideBadge } from "../components/domain/Badges";
import { KillSwitchButton } from "../components/domain/Controls";
import { TradesTable } from "../components/domain/Tables";
import { Badge, SimulatedTag } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState, Notice, SkeletonRows } from "../components/ui/Feedback";
import { Meter } from "../components/ui/Form";
import { Kpi } from "../components/ui/Kpi";
import { DataTable } from "../components/ui/Table";
import { fmtAgo, fmtDateTime, fmtNum, fmtPct, fmtPrice, fmtSignedUsd, fmtTime, fmtUsd, titleCase, toneClass, toneOf } from "../lib/format";
import { useDashboard } from "../lib/queries";
import type { Dashboard, LivePosition } from "../lib/types";

export function Overview() {
  const q = useDashboard();
  if (q.isError && !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  const loading = !d;
  const pf = d?.portfolio ?? null;
  const perf = d?.performance ?? null;
  const session = d?.bot.session ?? null;
  const initial = session?.initial_cash;

  return (
    <div className="space-y-4">
      <StatusStrip d={d} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <Kpi label="Equity" loading={loading} value={pf ? fmtUsd(pf.equity) : "—"} badge={<SimulatedTag />}
          sub={pf ? `${fmtPct(d?.returns.since_start_pct, 2, true)} since start` : "no paper session"} tone={toneOf(d?.returns.since_start_pct)} />
        <Kpi label="Cash" loading={loading} value={pf ? fmtUsd(pf.cash) : "—"} sub={pf ? `${fmtPct(pf.exposure_pct, 1)} of equity invested` : undefined} />
        <Kpi label="Realized PnL" loading={loading} value={pf ? fmtSignedUsd(pf.realized_pnl) : "—"} tone={toneOf(pf?.realized_pnl)} sub={pf ? `fees ${fmtUsd(pf.fees_paid)}` : undefined} />
        <Kpi label="Unrealized PnL" loading={loading} value={pf ? fmtSignedUsd(pf.unrealized_pnl) : "—"} tone={toneOf(pf?.unrealized_pnl)} sub={pf ? `${pf.positions} open position${pf.positions === 1 ? "" : "s"}` : undefined} />
        <Kpi label="Daily return" loading={loading} value={fmtPct(d?.returns.daily_pct, 2, true)} tone={toneOf(d?.returns.daily_pct)} sub={d?.returns.basis ? `UTC day · ${d.returns.basis}` : undefined}
          hint="Change of simulated equity since the start of the current UTC day (of the replay clock in a historical replay)." />
        <Kpi label="Weekly / monthly" loading={loading} value={<span>{fmtPct(d?.returns.weekly_pct, 2, true)}</span>} tone={toneOf(d?.returns.weekly_pct)} sub={`30 days ${fmtPct(d?.returns.monthly_pct, 2, true)}`} />
        <Kpi label="Win rate" loading={loading} value={fmtPct(perf?.win_rate_pct, 1)} sub={perf ? `${perf.trades} completed trade${perf.trades === 1 ? "" : "s"}` : undefined} />
        <Kpi label="Profit factor" loading={loading} value={fmtNum(perf?.profit_factor, 2)} hint="Gross profit / gross loss after costs. Undefined without losing trades." />
        <Kpi label="Expectancy" loading={loading} value={perf?.expectancy_r === null || perf?.expectancy_r === undefined ? "—" : `${fmtNum(perf.expectancy_r, 2)} R`} tone={toneOf(perf?.expectancy_r)} sub={perf ? `${fmtSignedUsd(perf.expectancy)} per trade` : undefined} />
        <Kpi label="Max drawdown" loading={loading} value={fmtPct(perf?.max_drawdown_pct, 2)} tone={perf?.max_drawdown_pct ? "down" : undefined} sub={`limit ${fmtPct(d?.risk.drawdown_limit_pct, 1)}`} />
        <Kpi label="Sharpe / Sortino" loading={loading} value={perf?.sharpe === null || perf?.sharpe === undefined ? "n/a" : `${fmtNum(perf.sharpe, 2)} / ${fmtNum(perf.sortino, 2)}`}
          sub={perf?.sharpe === null || perf?.sharpe === undefined ? "needs 30+ daily returns" : "daily, annualised"} hint="Shown only when there are at least 30 daily returns; otherwise the ratio is not meaningful." />
        <Kpi label="Open positions" loading={loading} value={pf ? String(pf.positions) : "—"} sub={pf ? `open risk ${fmtUsd(pf.open_risk)}` : undefined} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Simulated equity" subtitle={session ? `${session.data_mode === "replay" ? "Historical replay" : "Paper trading"} session ${session.id}` : "No session"} className="xl:col-span-2" actions={<SimulatedTag />}>
          {loading ? <SkeletonRows rows={8} /> : <EquityChart points={d?.equity} initial={initial} height={280} empty="Equity appears here once a paper session processes candles" />}
        </Card>
        <RegimeCard d={d} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <RiskCard d={d} />
        <FeedCard d={d} />
        <BotCard d={d} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Open positions" subtitle="Live positions of the current session" actions={<Link to="/portfolio" className="text-xs text-accent hover:underline">Portfolio</Link>}>
          <PositionsMini positions={d?.positions} loading={loading} />
        </Card>
        <Card title="Recent trades" subtitle="Completed simulated trades, newest first" actions={<Link to="/portfolio" className="text-xs text-accent hover:underline">All trades</Link>}>
          <TradesTable compact trades={d?.recent_trades} loading={loading} maxHeight={320} empty="No completed trades in this session yet" />
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Recent execution events" subtitle="Simulated orders: fills, cancellations and rejections">
          <DataTable
            rows={d?.orders}
            loading={loading}
            rowKey={(o) => o.client_order_id}
            dense
            maxHeight={320}
            empty={<EmptyState title="No orders yet" />}
            columns={[
              { key: "t", header: "Time", render: (o) => fmtDateTime(o.filled_ts ?? o.created_ts) },
              { key: "pair", header: "Pair", render: (o) => o.pair },
              { key: "side", header: "Side", render: (o) => <SideBadge side={o.side} /> },
              { key: "purpose", header: "Purpose", render: (o) => o.purpose },
              { key: "status", header: "Status", render: (o) => <span title={o.reject_reason ?? o.reason ?? ""}><OrderStatusBadge status={o.status} /></span> },
              { key: "px", header: "Fill", align: "right", render: (o) => fmtPrice(o.fill_price) },
            ]}
          />
        </Card>
        <Card title="Signals and rejections" subtitle="Entry and exit decisions with the reason recorded by the risk manager" actions={<Link to="/risk" className="text-xs text-accent hover:underline">Risk checks</Link>}>
          {loading ? (
            <SkeletonRows />
          ) : !d?.events.length ? (
            <EmptyState title="No signals yet">Entry signals appear here with every risk check that approved or rejected them.</EmptyState>
          ) : (
            <ul className="max-h-[320px] space-y-1.5 overflow-auto pr-1">
              {d.events.map((e, i) => (
                <li key={i} className="flex items-start gap-2 rounded-lg border border-border bg-surface-2/40 px-3 py-2 text-xs">
                  <Badge tone={e.action === "exit" ? "info" : e.accepted ? "up" : "down"}>{e.action === "exit" ? "exit" : e.accepted ? "accepted" : "rejected"}</Badge>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 text-text">
                      <span className="font-medium">{e.pair}</span>
                      {e.strategy && <span className="text-muted">{titleCase(e.strategy)}</span>}
                      <RegimeBadge regime={e.regime} />
                      <span className="num ml-auto text-faint">{fmtDateTime(e.ts)}</span>
                    </div>
                    <div className="mt-0.5 break-words text-muted">{e.reason}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {d && <p className="text-[11px] text-faint">{d.notice}</p>}
    </div>
  );
}

function StatusStrip({ d }: { d: Dashboard | undefined }) {
  if (!d) return null;
  const s = d.bot.session;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <EngineBadge status={d.bot.status} />
      {d.bot.label && <Badge tone={s?.data_mode === "replay" ? "warn" : "accent"}>{d.bot.label}</Badge>}
      {d.bot.kill_switch.on && <Badge tone="down">Kill switch ON</Badge>}
      {d.risk.daily_tripped && <Badge tone="down">Daily loss limit reached</Badge>}
      {d.risk.drawdown_tripped && <Badge tone="down">Drawdown limit reached</Badge>}
      {d.bot.engine && !d.bot.engine.healthy && (d.bot.status === "running" || d.bot.status === "paused") && (
        <Badge tone="warn" title={d.bot.engine.problems.join("; ")}>Engine degraded</Badge>
      )}
      {!s && (
        <Link to="/paper" className="ml-auto">
          <Button size="sm" variant="primary" icon={<CirclePlay className="size-3.5" />}>
            Start paper trading
          </Button>
        </Link>
      )}
      {d.bot.last_error && (
        <Notice tone="down" icon={<CircleAlert className="size-3.5" />} className="w-full">
          {d.bot.last_error}
        </Notice>
      )}
    </div>
  );
}

function RegimeCard({ d }: { d: Dashboard | undefined }) {
  const session = d?.bot.session;
  const engineView = !!session && (d?.bot.status === "running" || d?.bot.status === "paused");
  const pairs = engineView
    ? session!.pairs.map((p) => [p, d!.engine_pairs[p]] as const)
    : Object.entries(d?.regimes.pairs ?? {}).map(([p, r]) => [p, r] as const);
  return (
    <Card
      title="Market regime"
      subtitle={!d ? undefined : engineView ? `As seen by the paper engine on its latest ${session!.timeframe} candle${session!.data_mode === "replay" ? " (replay clock)" : ""}` : `Latest stored closed ${d.regimes.timeframe} candle of each pair`}
      actions={<Link to="/strategies" className="text-xs text-accent hover:underline">Details</Link>}
    >
      {!d ? (
        <SkeletonRows rows={4} />
      ) : !pairs.length ? (
        <EmptyState title="No pairs configured" />
      ) : (
        <div className="space-y-3">
          {pairs.map(([pair, info]) => {
            const r = info as { available?: boolean; regime?: string; confidence?: number; regime_confidence?: number; reason?: string; regime_reason?: string; candle_ts?: number; last_candle_ts?: number; stale?: boolean } | undefined;
            const regime = r?.regime;
            const confidence = r?.confidence ?? r?.regime_confidence;
            const reason = r?.reason ?? r?.regime_reason ?? "waiting for the first processed candle";
            const ts = r?.candle_ts ?? r?.last_candle_ts;
            const inSession = !!session && session.pairs.includes(pair);
            const active = d.active_strategies[pair];
            return (
              <div key={pair} className="rounded-lg border border-border bg-surface-2/40 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[13px] font-semibold">{pair}</span>
                  {regime && r?.available !== false ? <RegimeBadge regime={regime} confidence={confidence} /> : <Badge>no data</Badge>}
                </div>
                <p className="mt-1 text-xs leading-5 text-muted">{reason}</p>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-faint">
                  {ts !== undefined && <span>candle {fmtDateTime(ts)}</span>}
                  {r?.stale && <Badge tone="warn">stale</Badge>}
                  <span>
                    Active strategy:{" "}
                    <b className="font-medium text-text">
                      {!session ? "no session" : !inSession ? "not in this session" : active?.strategy ? titleCase(active.strategy) : "none (no trade)"}
                    </b>
                  </span>
                  {inSession && active?.version && <span className="break-all">{active.version}</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function RiskCard({ d }: { d: Dashboard | undefined }) {
  const r = d?.risk;
  return (
    <Card title="Risk utilisation" subtitle="Current use of each limit" actions={<Link to="/risk" className="text-xs text-accent hover:underline">Risk</Link>}>
      {!r ? (
        <SkeletonRows rows={4} />
      ) : (
        <div className="space-y-3.5">
          <Meter label="Open risk (sum of stop distances)" value={r.open_risk_pct} max={r.open_risk_limit_pct} detail={`${fmtPct(r.open_risk_pct, 2)} / ${fmtPct(r.open_risk_limit_pct, 2)}`} />
          <Meter label="Total exposure" value={r.exposure_pct} max={r.exposure_limit_pct} detail={`${fmtPct(r.exposure_pct, 1)} / ${fmtPct(r.exposure_limit_pct, 0)}`} />
          <Meter label="Daily loss" value={r.daily_loss_pct} max={r.daily_loss_limit_pct} tone="warn" detail={`${fmtPct(r.daily_loss_pct, 2)} / ${fmtPct(r.daily_loss_limit_pct, 2)}`} />
          <Meter label="Drawdown from peak" value={r.drawdown_pct} max={r.drawdown_limit_pct} tone="warn" detail={`${fmtPct(r.drawdown_pct, 2)} / ${fmtPct(r.drawdown_limit_pct, 2)}`} />
          {(r.daily_tripped || r.drawdown_tripped) && (
            <Notice tone="down" icon={<ShieldAlert className="size-3.5" />}>
              {r.daily_tripped ? "The daily loss limit stopped new entries until the next UTC day. " : ""}
              {r.drawdown_tripped ? "The drawdown limit stopped new entries until a person resets it." : ""}
            </Notice>
          )}
        </div>
      )}
    </Card>
  );
}

function FeedCard({ d }: { d: Dashboard | undefined }) {
  const m = d?.market;
  return (
    <Card title="Binance data feed" subtitle="Public market data, no API key" actions={<Link to="/market" className="text-xs text-accent hover:underline">Market</Link>}>
      {!m ? (
        <SkeletonRows rows={4} />
      ) : (
        <div className="space-y-3 text-xs">
          <div className="flex flex-wrap gap-2">
            <Badge tone={m.websocket ? "up" : "down"}>{m.websocket ? <Radio className="size-3" /> : <WifiOff className="size-3" />} WebSocket {m.websocket ? "connected" : "not connected"}</Badge>
            <Badge tone={m.rest_reachable ? "up" : m.rest_reachable === false ? "down" : "neutral"}>REST {m.rest_reachable ? "reachable" : m.rest_reachable === false ? "unreachable" : "not checked"}</Badge>
          </div>
          {m.rest_error && <p className="break-words text-down/90">{m.rest_error}</p>}
          <div className="space-y-1">
            {m.series.length === 0 && <p className="text-muted">No candles stored for the configured pairs yet.</p>}
            {m.series.map((s) => (
              <div key={`${s.pair}-${s.timeframe}`} className="flex items-center justify-between gap-2">
                <span className="text-muted">{s.pair} {s.timeframe}</span>
                <span className="flex items-center gap-2">
                  <span className="num text-faint">{s.last_close_ts ? `closed ${fmtAgo(s.age_s)}` : "empty"}</span>
                  <Badge tone={s.status === "fresh" ? "up" : s.status === "stale" ? "warn" : "neutral"}>{s.status}</Badge>
                </span>
              </div>
            ))}
          </div>
          <div className="space-y-1 border-t border-border pt-2">
            {Object.entries(m.tickers).map(([pair, t]) => (
              <div key={pair} className="flex items-center justify-between gap-2">
                <span className="text-muted">{pair}</span>
                {t ? (
                  <span className="num">
                    {fmtPrice(t.last)} <span className={toneClass(t.change_pct_24h, "text-muted")}>{fmtPct(t.change_pct_24h, 2, true)}</span>{" "}
                    <span className="text-faint">{fmtTime(t.received_ms)}</span>
                  </span>
                ) : (
                  <span className="text-faint">no live price</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function BotCard({ d }: { d: Dashboard | undefined }) {
  const s = d?.bot.session;
  return (
    <Card title="Bot status" subtitle="Paper trading engine" actions={d && <KillSwitchButton state={d.bot.kill_switch} size="sm" />}>
      {!d ? (
        <SkeletonRows rows={4} />
      ) : !s ? (
        <EmptyState title="No paper session" action={<Link to="/paper"><Button size="sm" variant="primary" icon={<ArrowRight className="size-3.5" />}>Paper Trading</Button></Link>}>
          Start a live paper session on Binance public data or a historical replay on stored candles. Earlier sessions stay available in Portfolio &amp; History and
          Performance Analytics.
        </EmptyState>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
          <dt className="text-muted">Status</dt>
          <dd><EngineBadge status={d.bot.status} /></dd>
          <dt className="text-muted">Mode</dt>
          <dd>{s.data_mode === "replay" ? "Historical replay" : "Live paper trading"} · {s.strategy_mode === "adaptive" ? "adaptive selection" : `forward test of ${titleCase(s.forward_strategy ?? "")}`}</dd>
          <dt className="text-muted">Pairs</dt>
          <dd>{s.pairs.join(", ")} · {s.timeframe} (confirm {s.confirm_timeframes.join(", ") || "—"})</dd>
          <dt className="text-muted">Clock</dt>
          <dd className="num">{fmtDateTime(s.clock_ts)}</dd>
          <dt className="text-muted">Started</dt>
          <dd className="num">{fmtDateTime(s.started_at)}</dd>
          <dt className="text-muted">Engine</dt>
          <dd>
            {d.bot.engine?.healthy ? (
              <span className="text-up">healthy</span>
            ) : d.bot.status === "running" || d.bot.status === "paused" ? (
              <span className="text-warn">{d.bot.engine?.problems.join("; ") || "unknown"}</span>
            ) : (
              <span className="text-muted">not running</span>
            )}
          </dd>
          <dt className="text-muted">Kill switch</dt>
          <dd>{d.bot.kill_switch.on ? <span className="text-down">ON · {d.bot.kill_switch.reason}</span> : "off"}</dd>
        </dl>
      )}
    </Card>
  );
}

function PositionsMini({ positions, loading }: { positions: LivePosition[] | undefined; loading: boolean }) {
  return (
    <DataTable
      rows={positions}
      loading={loading}
      rowKey={(p) => p.key}
      dense
      maxHeight={320}
      empty={<EmptyState title="No open positions" />}
      columns={[
        { key: "pair", header: "Pair", render: (p) => <span className="font-medium">{p.pair}</span> },
        { key: "strategy", header: "Strategy", render: (p) => titleCase(p.strategy) },
        { key: "qty", header: "Qty", align: "right", render: (p) => fmtNum(p.qty - p.exit_qty, 6) },
        { key: "entry", header: "Entry", align: "right", render: (p) => fmtPrice(p.entry_price) },
        { key: "stop", header: "Stop", align: "right", render: (p) => <span className="text-down">{fmtPrice(p.stop)}</span> },
        { key: "target", header: "Target", align: "right", render: (p) => <span className="text-up">{fmtPrice(p.target)}</span> },
        { key: "risk", header: "Risk", align: "right", render: (p) => fmtUsd(p.initial_risk) },
        { key: "opened", header: "Opened", render: (p) => fmtDateTime(p.entry_ts) },
      ]}
    />
  );
}
