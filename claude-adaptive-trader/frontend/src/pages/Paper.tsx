// Paper trading: start a live session on Binance public data or a historical replay on stored
// candles, control it, and follow every decision, order, position and the simulated equity.
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CirclePause, CirclePlay, CircleStop, CircleX, ListFilter, Play } from "lucide-react";
import { EquityChart } from "../components/charts/Charts";
import { PriceChart, type ChartLevel, type ChartMarker, type ChartToggles } from "../components/charts/PriceChart";
import { EngineBadge, OrderStatusBadge, RegimeBadge, SideBadge } from "../components/domain/Badges";
import { ChecksList, SizingDetail } from "../components/domain/Checks";
import { ENGINE_KEYS, KillSwitchButton } from "../components/domain/Controls";
import { Badge, SimulatedTag } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog, Dialog } from "../components/ui/Dialog";
import { EmptyState, ErrorState, Notice, Skeleton, SkeletonRows } from "../components/ui/Feedback";
import { Field, Input, Segmented, Select, Tabs } from "../components/ui/Form";
import { Kpi } from "../components/ui/Kpi";
import { DataTable } from "../components/ui/Table";
import { get, post, qs } from "../lib/api";
import { fmtDateTime, fmtNum, fmtPct, fmtPrice, fmtSignedUsd, fmtUsd, titleCase, toneOf } from "../lib/format";
import { useDecisions, useEquity, useInventory, usePaperStatus, usePairs, useSessions, useStrategies } from "../lib/queries";
import type { ChartPayload, Decision, PaperStatus } from "../lib/types";
import { useAction } from "../lib/useAction";
import { useLiveRefresh } from "../lib/useLiveRefresh";
import { usePersistent } from "../lib/usePersistent";

const PAPER_TOGGLES: ChartToggles = { volume: true, ema20: true, ema50: true, ema200: false, bb: false, rsi: false, atr: false, markers: true, levels: true };

export function PaperPage() {
  const status = usePaperStatus();
  useLiveRefresh("paper", ENGINE_KEYS);
  if (status.isError && !status.data) return <ErrorState error={status.error} onRetry={() => status.refetch()} />;
  const st = status.data;
  const active = st && (st.status === "running" || st.status === "paused");

  return (
    <div className="space-y-4">
      {!st ? (
        <Skeleton className="h-40 w-full" />
      ) : active ? (
        <SessionControls st={st} />
      ) : (
        <>
          {st.session && <LastSessionNote st={st} />}
          <StartForm />
        </>
      )}
      {st?.session && <SessionView st={st} />}
      <SessionsHistory />
    </div>
  );
}

function LastSessionNote({ st }: { st: PaperStatus }) {
  return (
    <Notice tone={st.status === "error" ? "down" : "info"} icon={<CircleAlert className="size-3.5" />}>
      Session {st.session?.id} is <b>{st.status}</b>
      {st.last_error ? `: ${st.last_error}` : ""}. Its records stay below and in Portfolio &amp; History; start a new session to trade again.
    </Notice>
  );
}

// ----- start --------------------------------------------------------------------------------------------

function StartForm() {
  const pairsInfo = usePairs();
  const inventory = useInventory();
  const strategies = useStrategies();
  const [dataMode, setDataMode] = usePersistent<"live" | "replay">("paper.dataMode", "live");
  const [strategyMode, setStrategyMode] = usePersistent<"adaptive" | "forward_test">("paper.strategyMode", "adaptive");
  const [strategy, setStrategy] = usePersistent("paper.strategy", "trend_following");
  const [exchange, setExchange] = usePersistent("paper.exchange", "binance");
  const [timeframe, setTimeframe] = usePersistent("paper.timeframe", "15m");
  const [pairs, setPairs] = usePersistent<string[]>("paper.pairs", []);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [speed, setSpeed] = useState("20");
  const [cash, setCash] = useState("");

  const series = inventory.data?.series ?? [];
  const exchanges = useMemo(() => Array.from(new Set(["binance", ...series.map((s) => s.exchange)])), [series]);
  const ex = dataMode === "live" ? "binance" : exchange;
  const available = useMemo(() => {
    if (dataMode === "live") return pairsInfo.data?.trading_pairs ?? pairsInfo.data?.pairs ?? [];
    return Array.from(new Set(series.filter((s) => s.exchange === ex).map((s) => s.pair))).sort();
  }, [dataMode, ex, series, pairsInfo.data]);
  const stored = useMemo(() => series.filter((s) => s.exchange === ex && (pairs.length ? pairs.includes(s.pair) : true)), [series, ex, pairs]);
  const chosen = pairs.filter((p) => available.includes(p));

  useEffect(() => {
    // Default to the configured pairs (live) or the first stored pair (replay).
    if (!available.length) return;
    if (!pairs.some((p) => available.includes(p))) setPairs(dataMode === "live" ? available.slice(0, 2) : available.slice(0, 1));
  }, [available, dataMode, pairs, setPairs]);
  useEffect(() => {
    if (dataMode === "replay" && stored.length && !stored.some((s) => s.timeframe === timeframe)) setTimeframe(stored[0].timeframe);
  }, [dataMode, stored, timeframe, setTimeframe]);

  const startAction = useAction(
    () =>
      post<PaperStatus>("/api/paper/start", {
        data_mode: dataMode,
        strategy_mode: strategyMode,
        strategy: strategyMode === "forward_test" ? strategy : null,
        pairs: chosen,
        timeframe,
        exchange: ex,
        replay_start: dataMode === "replay" && start ? start : null,
        replay_end: dataMode === "replay" && end ? end : null,
        replay_speed: dataMode === "replay" && speed ? Number(speed) : null,
        initial_cash: cash ? Number(cash) : null,
      }),
    { success: (r) => `Session ${r.session?.id} started`, invalidate: ENGINE_KEYS, errorTitle: "Could not start the session" },
  );

  const timeframes = dataMode === "live" ? pairsInfo.data?.timeframes ?? ["15m"] : Array.from(new Set(stored.map((s) => s.timeframe)));
  const range = stored.filter((s) => s.timeframe === timeframe);

  return (
    <Card title="Start a paper session" subtitle="Simulated orders only. No exchange account, no API key and no real money are involved." actions={<SimulatedTag />}>
      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="space-y-4">
          <Field label="Market data" group>
            <Segmented
              value={dataMode}
              onChange={setDataMode}
              options={[
                { value: "live", label: "Live Binance data", title: "Closed candles from Binance public data, as they complete" },
                { value: "replay", label: "Historical replay", title: "Candle by candle over stored history, clearly labelled as a replay" },
              ]}
            />
          </Field>
          <Field label="Strategy selection" group>
            <Segmented
              value={strategyMode}
              onChange={setStrategyMode}
              options={[
                { value: "adaptive", label: "Adaptive selector", title: "Only approved versions with out-of-sample evidence in the current regime" },
                { value: "forward_test", label: "Forward test one strategy", title: "Run one strategy on paper to collect forward evidence" },
              ]}
            />
          </Field>
          {strategyMode === "forward_test" ? (
            <Field label="Strategy" hint="Uses the approved version's parameters when there is one, otherwise the defaults.">
              <Select value={strategy} onChange={(e) => setStrategy(e.target.value)} options={(strategies.data ?? []).map((s) => ({ value: s.name, label: `${s.title} (${s.name})` }))} />
            </Field>
          ) : (
            <Notice>
              The adaptive selector trades only approved strategy versions that have enough out-of-sample evidence in the current regime. With none eligible it
              records &ldquo;no trade&rdquo; on every candle, which is the intended behaviour.
            </Notice>
          )}
        </div>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            {dataMode === "replay" && (
              <Field label="Exchange (stored data)">
                <Select value={ex} onChange={(e) => setExchange(e.target.value)} options={exchanges.map((x) => ({ value: x, label: x }))} />
              </Field>
            )}
            <Field label="Signal timeframe">
              <Select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} options={(timeframes.length ? timeframes : ["15m"]).map((t) => ({ value: t, label: t }))} />
            </Field>
            <Field label="Initial virtual cash (USDT)" hint="Default from settings">
              <Input type="number" min={1} value={cash} onChange={(e) => setCash(e.target.value)} placeholder="10000" />
            </Field>
          </div>
          <Field label="Pairs" group>
            <div className="flex flex-wrap gap-1.5">
              {available.length === 0 && <span className="text-xs text-muted">{dataMode === "replay" ? "No stored candles for this exchange. Import data on the Market page." : "Loading pairs…"}</span>}
              {available.map((p) => {
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
          {dataMode === "replay" && (
            <>
              <div className="grid grid-cols-3 gap-3">
                <Field label="Replay from (UTC)" hint="Empty: after a warm-up">
                  <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
                </Field>
                <Field label="Replay to (UTC)" hint="Empty: last stored candle">
                  <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
                </Field>
                <Field label="Speed (candles/s)">
                  <Input type="number" min={1} max={10000} value={speed} onChange={(e) => setSpeed(e.target.value)} />
                </Field>
              </div>
              {range.length > 0 && (
                <p className="text-[11px] text-faint">
                  Stored: {range.map((s) => `${s.pair} ${fmtDateTime(s.first_ts)} → ${fmtDateTime(s.last_ts)} (${s.candles} candles)`).join(" · ")}
                </p>
              )}
            </>
          )}
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-end gap-3 border-t border-border pt-4">
        <span className="text-xs text-muted">
          {dataMode === "live" ? "Trades are decided on each closed candle and filled at the next open with modelled costs." : "A replay is labelled HISTORICAL REPLAY everywhere and never mixed with live paper results."}
        </span>
        <Button variant="primary" icon={<Play className="size-4" />} loading={startAction.isPending} disabled={!chosen.length} onClick={() => startAction.mutate(undefined)}>
          Start {dataMode === "replay" ? "replay" : "paper trading"}
        </Button>
      </div>
    </Card>
  );
}

// ----- running session ------------------------------------------------------------------------------------

function SessionControls({ st }: { st: PaperStatus }) {
  const [closeOpen, setCloseOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const pause = useAction(() => post("/api/paper/pause"), { success: "Session paused", invalidate: ENGINE_KEYS });
  const resume = useAction(() => post("/api/paper/resume"), { success: "Session resumed", invalidate: ENGINE_KEYS });
  const stop = useAction(() => post("/api/paper/stop"), { success: "Session stopped", invalidate: ENGINE_KEYS, onSuccess: () => setStopOpen(false) });
  const closeAll = useAction((reason: string) => post<{ closing: number }>("/api/paper/close-all", { reason }), {
    success: "Exit orders created for all open positions",
    invalidate: ENGINE_KEYS,
    onSuccess: () => setCloseOpen(false),
  });
  const s = st.session!;
  return (
    <Card
      title={<span className="flex items-center gap-2">{st.label} <EngineBadge status={st.status} /></span>}
      subtitle={`Session ${s.id} · ${s.strategy_mode === "adaptive" ? "adaptive selector" : `forward test of ${titleCase(s.forward_strategy ?? "")}`} · ${s.pairs.join(", ")} · ${s.timeframe}`}
      actions={<SimulatedTag />}
    >
      <div className="flex flex-wrap items-center gap-2">
        {st.status === "running" ? (
          <Button icon={<CirclePause className="size-4" />} loading={pause.isPending} onClick={() => pause.mutate(undefined)}>
            Pause
          </Button>
        ) : (
          <Button variant="primary" icon={<CirclePlay className="size-4" />} loading={resume.isPending} onClick={() => resume.mutate(undefined)}>
            Resume
          </Button>
        )}
        <Button icon={<CircleStop className="size-4" />} onClick={() => setStopOpen(true)}>
          Stop session
        </Button>
        <Button variant="danger" icon={<CircleX className="size-4" />} disabled={!st.positions?.length} onClick={() => setCloseOpen(true)}>
          Close all positions
        </Button>
        <KillSwitchButton state={st.kill_switch} />
        <span className="ml-auto text-xs text-muted">
          {s.data_mode === "replay" ? (
            <>
              Replay clock <b className="num text-text">{fmtDateTime(s.clock_ts)}</b> · {fmtDateTime(s.replay_start_ts)} → {fmtDateTime(s.replay_end_ts)} at {s.replay_speed} candles/s
            </>
          ) : (
            <>Started {fmtDateTime(s.started_at)}</>
          )}
        </span>
      </div>
      {st.engine && !st.engine.healthy && (
        <Notice tone="warn" className="mt-3" icon={<CircleAlert className="size-3.5" />}>
          New entries are blocked while the engine is degraded: {st.engine.problems.join("; ")}
        </Notice>
      )}
      <ConfirmDialog open={stopOpen} onClose={() => setStopOpen(false)} title="Stop this session?" confirmLabel="Stop" danger busy={stop.isPending} onConfirm={() => stop.mutate(undefined)}>
        <p>The engine stops processing candles. Open positions stay as recorded (they are not sold). A stopped session cannot be resumed; its history is kept.</p>
      </ConfirmDialog>
      <ConfirmDialog
        open={closeOpen}
        onClose={() => setCloseOpen(false)}
        title="Close all open positions?"
        confirmLabel="Close all"
        danger
        busy={closeAll.isPending}
        requireNote="Reason (recorded with the exit)"
        onConfirm={(note) => closeAll.mutate(note)}
      >
        <p>
          A market exit order is created for every open position and filled at the next candle&apos;s open with the usual simulated costs. This does not stop the
          session; use the kill switch to block new entries as well.
        </p>
      </ConfirmDialog>
    </Card>
  );
}

function SessionView({ st }: { st: PaperStatus }) {
  const s = st.session!;
  const pf = st.portfolio;
  const [tab, setTab] = usePersistent<"positions" | "orders" | "decisions" | "equity">("paper.tab", "decisions");
  const [pair, setPair] = useState(s.pairs[0]);
  useEffect(() => {
    if (!s.pairs.includes(pair)) setPair(s.pairs[0]);
  }, [s.pairs, pair]);
  const equity = useEquity("paper", s.id);
  const stats = st.stats;

  return (
    <>
      {pf && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 2xl:grid-cols-8">
          <Kpi label="Equity" value={fmtUsd(pf.equity)} sub={fmtPct((pf.equity / s.initial_cash - 1) * 100, 2, true)} tone={toneOf(pf.equity - s.initial_cash)} badge={<SimulatedTag />} />
          <Kpi label="Cash" value={fmtUsd(pf.cash)} />
          <Kpi label="Realized" value={fmtSignedUsd(pf.realized_pnl)} tone={toneOf(pf.realized_pnl)} />
          <Kpi label="Unrealized" value={fmtSignedUsd(pf.unrealized_pnl)} tone={toneOf(pf.unrealized_pnl)} />
          <Kpi label="Exposure" value={fmtPct(pf.exposure_pct, 1)} sub={fmtUsd(pf.exposure)} />
          <Kpi label="Open risk" value={fmtUsd(pf.open_risk)} sub={`${fmtPct((pf.open_risk / pf.equity) * 100, 2)} of equity`} />
          <Kpi label="Fees paid" value={fmtUsd(pf.fees_paid)} />
          <Kpi label="Slippage (est.)" value={fmtUsd(pf.slippage_paid)} />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <Card
          title="Chart with simulated trades"
          subtitle={s.data_mode === "replay" ? "Stored candles up to the replay clock" : "Latest closed candles"}
          actions={
            <Segmented size="sm" value={pair} onChange={setPair} options={s.pairs.map((p) => ({ value: p, label: p }))} />
          }
          bodyClassName="p-2"
        >
          <SessionChart st={st} pair={pair} />
        </Card>
        <Card title="Engine by pair" subtitle="Last processed closed candle">
          <div className="space-y-2.5">
            {s.pairs.map((p) => {
              const ps = st.pairs?.[p];
              return (
                <div key={p} className="rounded-lg border border-border bg-surface-2/40 p-2.5 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold">{p}</span>
                    {ps?.regime ? <RegimeBadge regime={ps.regime} confidence={ps.regime_confidence} /> : <Badge>waiting</Badge>}
                  </div>
                  <div className="mt-1 text-muted">
                    {ps?.last_candle_ts ? (
                      <>
                        candle {fmtDateTime(ps.last_candle_ts)} · close <span className="num text-text">{fmtPrice(ps.close)}</span>
                      </>
                    ) : (
                      "no candle processed yet"
                    )}
                  </div>
                  {ps?.waiting && <div className="mt-0.5 text-faint">{ps.waiting}</div>}
                  {ps?.regime_reason && <div className="mt-0.5 text-faint">{ps.regime_reason}</div>}
                </div>
              );
            })}
            {stats && (
              <div className="rounded-lg border border-border p-2.5 text-xs">
                <div className="mb-1 font-medium">Decisions this run</div>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(stats.decisions).map(([k, v]) => (
                    <Badge key={k}>
                      {k.replace("_", " ")} <span className="num">{v}</span>
                    </Badge>
                  ))}
                </div>
                {Object.keys(stats.rejections).length > 0 && (
                  <>
                    <div className="mb-1 mt-2 font-medium">First failed check of rejected signals</div>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(stats.rejections).map(([k, v]) => (
                        <Badge key={k} tone="down">
                          {titleCase(k)} <span className="num">{v}</span>
                        </Badge>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </Card>
      </div>

      <Card bodyClassName="p-0">
        <div className="px-4 pt-2">
          <Tabs
            value={tab}
            onChange={setTab}
            tabs={[
              { value: "decisions", label: "Decisions" },
              { value: "positions", label: `Open positions (${st.positions?.length ?? 0})` },
              { value: "orders", label: `Pending orders (${st.pending_orders?.length ?? 0})` },
              { value: "equity", label: "Equity" },
            ]}
          />
        </div>
        <div className="p-4">
          {tab === "decisions" && <DecisionsFeed sessionId={s.id} pairs={s.pairs} />}
          {tab === "positions" && (
            <DataTable
              rows={st.positions}
              rowKey={(p) => p.key}
              dense
              empty={<EmptyState title="No open positions" />}
              columns={[
                { key: "pair", header: "Pair", render: (p) => <span className="font-medium">{p.pair}</span> },
                { key: "strategy", header: "Strategy", render: (p) => titleCase(p.strategy) },
                { key: "regime", header: "Regime", render: (p) => <RegimeBadge regime={p.regime} /> },
                { key: "qty", header: "Quantity", align: "right", render: (p) => fmtNum(p.qty - p.exit_qty, 6) },
                { key: "entry", header: "Entry", align: "right", render: (p) => fmtPrice(p.entry_price) },
                { key: "stop", header: "Stop", align: "right", render: (p) => <span className="text-down">{fmtPrice(p.stop)}</span> },
                { key: "target", header: "Target", align: "right", render: (p) => <span className="text-up">{fmtPrice(p.target)}</span> },
                { key: "mark", header: "Last close", align: "right", render: (p) => fmtPrice(st.pairs?.[p.pair]?.close) },
                {
                  key: "upnl",
                  header: "Unrealized",
                  align: "right",
                  render: (p) => {
                    const close = st.pairs?.[p.pair]?.close;
                    if (close === undefined) return "—";
                    const v = (p.qty - p.exit_qty) * (close - p.entry_price);
                    return <span className={v >= 0 ? "text-up" : "text-down"}>{fmtSignedUsd(v)}</span>;
                  },
                },
                { key: "bars", header: "Candles", align: "right", render: (p) => p.bars_held },
                { key: "opened", header: "Opened", render: (p) => fmtDateTime(p.entry_ts) },
                { key: "exit", header: "", render: (p) => (p.pending_exit ? <Badge tone="warn">exit pending</Badge> : null) },
              ]}
            />
          )}
          {tab === "orders" && (
            <DataTable
              rows={st.pending_orders}
              rowKey={(o) => o.client_order_id}
              dense
              empty={<EmptyState title="No pending orders">Orders decided at a candle close wait here for the next candle&apos;s open.</EmptyState>}
              columns={[
                { key: "t", header: "Decided", render: (o) => fmtDateTime(o.created_ts) },
                { key: "pair", header: "Pair", render: (o) => o.pair },
                { key: "side", header: "Side", render: (o) => <SideBadge side={o.side} /> },
                { key: "purpose", header: "Purpose", render: (o) => o.purpose },
                { key: "qty", header: "Quantity", align: "right", render: (o) => fmtNum(o.qty, 6) },
                { key: "ref", header: "Decision close", align: "right", render: (o) => fmtPrice(o.ref_price) },
                { key: "stop", header: "Stop", align: "right", render: (o) => fmtPrice(o.stop) },
                { key: "status", header: "Status", render: (o) => <OrderStatusBadge status={o.status} /> },
                { key: "why", header: "Reason", render: (o) => <span className="block max-w-[320px] truncate text-muted" title={o.reason}>{o.reason}</span> },
              ]}
            />
          )}
          {tab === "equity" && <EquityChart points={equity.data?.points} initial={s.initial_cash} height={300} />}
        </div>
      </Card>
    </>
  );
}

function SessionChart({ st, pair }: { st: PaperStatus; pair: string }) {
  const s = st.session!;
  const replay = s.data_mode === "replay";
  const clock = replay ? s.clock_ts : null;
  const candles = useQuery({
    queryKey: ["paper-candles", s.exchange, pair, s.timeframe, replay ? "replay" : "live", clock ? Math.floor(clock / 60_000) : 0],
    queryFn: ({ signal }) =>
      get<ChartPayload>(`/api/market/candles${qs({ pair, timeframe: s.timeframe, exchange: s.exchange, limit: 300, end: clock ?? undefined, refresh: !replay })}`, signal),
    refetchInterval: replay ? false : 30_000,
    placeholderData: (prev) => prev,
    retry: 1,
  });
  const annotations = useQuery({
    queryKey: ["paper-annotations", s.id, pair, s.timeframe],
    queryFn: ({ signal }) => get<{ markers: ChartMarker[]; levels: ChartLevel[] }>(`/api/paper/chart-annotations${qs({ pair, timeframe: s.timeframe, session_id: s.id })}`, signal),
    refetchInterval: 5_000,
  });
  if (candles.isError && !candles.data) return <ErrorState error={candles.error} onRetry={() => candles.refetch()} />;
  if (!candles.data) return <Skeleton className="h-[420px] w-full" />;
  if (!candles.data.candles.length) return <EmptyState title="No stored candles for this pair yet" className="h-[420px]" />;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-2 pb-2 text-[11px] text-muted">
        <Badge tone={replay ? "warn" : "info"}>{candles.data.data_label}</Badge>
        <span>
          <span className="text-up">▲</span> buy fills · <span className="text-down">▼</span> sell fills · <span className="text-accent">●</span> accepted signal · <span className="text-muted">●</span> rejected signal
        </span>
      </div>
      <PriceChart payload={candles.data} toggles={PAPER_TOGGLES} markers={annotations.data?.markers} levels={annotations.data?.levels} height={420} />
    </div>
  );
}

function DecisionsFeed({ sessionId, pairs }: { sessionId: string; pairs: string[] }) {
  const [pair, setPair] = useState("");
  const [action, setAction] = usePersistent("paper.decisionAction", "enter");
  const [open, setOpen] = useState<Decision | null>(null);
  const q = useDecisions({ session_id: sessionId, pair: pair || undefined, action: action || undefined, limit: 300 });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ListFilter className="size-4 text-faint" />
        <div className="w-36">
          <Select value={pair} onChange={(e) => setPair(e.target.value)} options={[{ value: "", label: "All pairs" }, ...pairs.map((p) => ({ value: p, label: p }))]} />
        </div>
        <Segmented
          value={action}
          onChange={setAction}
          options={[
            { value: "enter", label: "Entry signals" },
            { value: "exit", label: "Exits" },
            { value: "no_trade", label: "No trade" },
            { value: "hold", label: "Hold" },
            { value: "", label: "All" },
          ]}
        />
        <span className="ml-auto text-xs text-muted">Click a row to see every risk check</span>
      </div>
      <DataTable
        rows={q.data}
        loading={q.isLoading}
        rowKey={(d) => d.id}
        dense
        maxHeight={460}
        onRowClick={setOpen}
        empty={<EmptyState title="No decisions recorded with this filter yet" />}
        columns={[
          { key: "ts", header: "Candle close", render: (d) => fmtDateTime(d.ts) },
          { key: "pair", header: "Pair", render: (d) => d.pair },
          {
            key: "action",
            header: "Decision",
            render: (d) => (
              <Badge tone={d.action === "enter" ? (d.accepted ? "up" : "down") : d.action === "exit" ? "info" : "neutral"}>
                {d.action === "enter" ? (d.accepted ? "entry accepted" : "entry rejected") : d.action.replace("_", " ")}
              </Badge>
            ),
          },
          { key: "strategy", header: "Strategy", render: (d) => (d.strategy ? titleCase(d.strategy) : "—") },
          { key: "regime", header: "Regime", render: (d) => <RegimeBadge regime={d.regime} confidence={d.regime_confidence} /> },
          { key: "reason", header: "Reason", render: (d) => <span className="block max-w-[520px] truncate text-muted" title={d.reason}>{d.reason}</span> },
        ]}
      />
      <Dialog open={!!open} onClose={() => setOpen(null)} title={open ? `${open.pair} · ${open.action.replace("_", " ")} · ${fmtDateTime(open.ts)}` : ""} width={760}>
        {open && <DecisionDetail d={open} />}
      </Dialog>
    </div>
  );
}

export function DecisionDetail({ d }: { d: Decision }) {
  const candidates = (d.signal?.scores as { candidates?: { strategy: string; eligible: boolean; score: number | null; reasons: string[] }[] } | null | undefined)?.candidates;
  return (
    <div className="space-y-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <RegimeBadge regime={d.regime} confidence={d.regime_confidence} />
        {d.strategy && <Badge>{titleCase(d.strategy)}</Badge>}
        {d.strategy_version && <span className="break-all text-faint">{d.strategy_version}</span>}
      </div>
      <p className="leading-5 text-text">{d.reason}</p>
      {d.signal?.entry_ref !== undefined && (
        <div className="grid grid-cols-2 gap-2 rounded-md border border-border bg-surface-2/40 p-2 sm:grid-cols-4">
          <span className="text-muted">Decision close <b className="num block text-text">{fmtPrice(d.signal.entry_ref)}</b></span>
          <span className="text-muted">Stop <b className="num block text-down">{fmtPrice(d.signal.stop)}</b></span>
          <span className="text-muted">Target <b className="num block text-up">{fmtPrice(d.signal.target)}</b></span>
          <span className="text-muted">Confidence <b className="num block text-text">{fmtPct((d.signal.confidence ?? 0) * 100, 0)}</b></span>
        </div>
      )}
      {d.checks && d.checks.length > 0 && (
        <div>
          <div className="mb-1 font-medium text-text">Risk checks ({d.checks.filter((c) => c.passed).length}/{d.checks.length} passed)</div>
          <ChecksList checks={d.checks} />
        </div>
      )}
      {d.signal?.sizing && <SizingDetail sizing={d.signal.sizing} />}
      {d.signal?.notes && d.signal.notes.length > 0 && (
        <ul className="list-disc pl-4 text-warn">
          {d.signal.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {candidates && candidates.length > 0 && (
        <div>
          <div className="mb-1 font-medium text-text">Selector candidates</div>
          <ul className="space-y-1">
            {candidates.map((c) => (
              <li key={c.strategy} className="rounded-md border border-border px-2 py-1">
                <span className="font-medium">{titleCase(c.strategy)}</span>{" "}
                {c.eligible ? <Badge tone="up">eligible · score {fmtNum(c.score, 3)}</Badge> : <Badge>not eligible</Badge>}
                {c.reasons.length > 0 && <div className="mt-0.5 text-muted">{c.reasons.join("; ")}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function SessionsHistory() {
  const q = useSessions();
  return (
    <Card title="Session history" subtitle="Every paper and replay session, kept for audit">
      {q.isLoading ? (
        <SkeletonRows />
      ) : (
        <DataTable
          rows={q.data}
          rowKey={(r) => r.id}
          dense
          maxHeight={320}
          empty={<EmptyState title="No sessions yet" />}
          columns={[
            { key: "id", header: "Session", render: (r) => <span className="font-medium">{r.id}</span> },
            { key: "created", header: "Created", render: (r) => fmtDateTime(r.created_at), sort: (r) => r.created_at },
            { key: "status", header: "Status", render: (r) => <EngineBadge status={r.status} /> },
            { key: "mode", header: "Mode", render: (r) => <Badge tone={r.data_mode === "replay" ? "warn" : "accent"}>{r.data_mode === "replay" ? "replay" : "live paper"}</Badge> },
            { key: "strategy", header: "Selection", render: (r) => (r.strategy_mode === "adaptive" ? "adaptive" : `forward test: ${titleCase(r.forward_strategy ?? "")}`) },
            { key: "pairs", header: "Pairs", render: (r) => `${r.pairs.join(", ")} · ${r.timeframe} · ${r.exchange}` },
            { key: "cash", header: "Start cash", align: "right", render: (r) => fmtUsd(r.initial_cash, 0) },
            { key: "err", header: "Note", render: (r) => <span className="block max-w-[260px] truncate text-muted" title={r.last_error ?? r.note ?? ""}>{r.last_error ?? r.note ?? ""}</span> },
          ]}
        />
      )}
    </Card>
  );
}
