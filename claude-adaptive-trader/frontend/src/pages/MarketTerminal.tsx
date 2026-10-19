import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Database, Download, FileDown, RefreshCw, SlidersHorizontal, Upload, Wifi, WifiOff } from "lucide-react";
import { PriceChart, DEFAULT_TOGGLES, type ChartLevel, type ChartMarker, type ChartToggles } from "../components/charts/PriceChart";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { EmptyState, ErrorState, Notice, Skeleton } from "../components/ui/Feedback";
import { Field, Input, Segmented, Select, Switch } from "../components/ui/Form";
import { DataTable } from "../components/ui/Table";
import { useToast } from "../components/ui/Toast";
import { ApiError, api, errorMessage, get, post, qs } from "../lib/api";
import { fmtAgo, fmtCompact, fmtDateTime, fmtNum, fmtPct, fmtPrice } from "../lib/format";
import { useKlineSubscription, useLiveEvents } from "../lib/live";
import { useCandles, useInventory, useJobs, useMarketStatus, useOrderBook, usePairs, useTickers } from "../lib/queries";
import type { Candle, InventoryRow, Job, Ticker } from "../lib/types";
import { usePersistent } from "../lib/usePersistent";

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

type Annotations = { markers: ChartMarker[]; levels: ChartLevel[] };

function useAnnotations(pair: string, timeframe: string, exchange: string) {
  return useQuery({
    queryKey: ["chart-annotations", pair, timeframe],
    queryFn: async ({ signal }) => {
      try {
        return await get<Annotations>(`/api/paper/chart-annotations${qs({ pair, timeframe })}`, signal);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return { markers: [], levels: [] };
        throw e;
      }
    },
    enabled: exchange === "binance",
    refetchInterval: 15_000,
    retry: 0,
  });
}

function useChartHeight(): number {
  const [h, setH] = useState(() => calc());
  function calc() {
    return Math.max(460, Math.min(780, window.innerHeight - 250));
  }
  useEffect(() => {
    const onResize = () => setH(calc());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return h;
}

export function MarketTerminal() {
  const pairs = usePairs();
  const chartHeight = useChartHeight();
  const [source, setSource] = usePersistent("market.source", "binance|BTC/USDT");
  const [timeframe, setTimeframe] = usePersistent("market.timeframe", "15m");
  const [toggles, setToggles] = usePersistent<ChartToggles>("market.toggles", DEFAULT_TOGGLES);
  const [showToggles, setShowToggles] = useState(false);
  const [exchange, pair] = source.split("|") as [string, string];
  const candles = useCandles(pair, timeframe, exchange);
  const annotations = useAnnotations(pair, timeframe, exchange);
  const tickers = useTickers();
  const [liveTicker, setLiveTicker] = useState<Record<string, Ticker>>({});
  const [liveCandle, setLiveCandle] = useState<Candle | null>(null);
  const qc = useQueryClient();

  useKlineSubscription(exchange === "binance" ? pair : null, timeframe);
  useEffect(() => setLiveCandle(null), [source, timeframe]);
  useLiveEvents((e) => {
    if (e.type === "ticker") {
      const t = e.data as Ticker;
      setLiveTicker((prev) => ({ ...prev, [t.pair]: t }));
    } else if (e.type === "kline" && e.pair === pair && e.timeframe === timeframe && exchange === "binance") {
      const c = e.candle as { ts: number; open: number; high: number; low: number; close: number; volume: number };
      setLiveCandle({ time: Math.floor(c.ts / 1000), ts: c.ts, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume });
      if (e.closed) void qc.invalidateQueries({ queryKey: ["candles", exchange, pair, timeframe] });
    }
  });

  const options = useMemo(() => {
    const out: { value: string; label: string }[] = [];
    for (const p of pairs.data?.pairs ?? []) out.push({ value: `binance|${p}`, label: `${p} · Binance` });
    const seen = new Set(out.map((o) => o.value));
    for (const s of pairs.data?.local_series ?? []) {
      const v = `${s.exchange}|${s.pair}`;
      if (!seen.has(v)) {
        seen.add(v);
        out.push({ value: v, label: `${s.pair} · ${s.exchange} (local data)` });
      }
    }
    if (!seen.has(source)) out.push({ value: source, label: `${pair} · ${exchange}` });
    return out;
  }, [pairs.data, source, pair, exchange]);

  const ticker = exchange === "binance" ? liveTicker[pair] ?? tickers.data?.tickers?.[pair] ?? null : null;
  const available = (pairs.data?.local_series ?? [])
    .filter((s) => s.exchange === exchange && s.pair === pair && s.timeframe !== timeframe)
    .map((s) => s.timeframe);
  const payload = candles.data;
  const fresh = payload?.freshness;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="w-full sm:w-64">
          <Select value={source} onChange={(e) => setSource(e.target.value)} options={options} aria-label="Trading pair" />
        </div>
        <Segmented value={timeframe} onChange={setTimeframe} options={TIMEFRAMES.map((t) => ({ value: t, label: t }))} />
        <Button variant="ghost" size="sm" icon={<SlidersHorizontal className="size-3.5" />} onClick={() => setShowToggles(true)}>
          Indicators
        </Button>
        <Button
          variant="ghost"
          size="sm"
          icon={<RefreshCw className={candles.isFetching ? "size-3.5 animate-spin" : "size-3.5"} />}
          onClick={() => void candles.refetch()}
        >
          Refresh
        </Button>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {payload && <Badge tone={payload.derived_from ? "warn" : payload.exchange === "binance" ? "info" : "neutral"}>{payload.data_label}</Badge>}
          {fresh && <FreshnessBadge status={fresh.status} age={fresh.age_s} />}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Card bodyClassName="p-0" className="overflow-hidden">
          {payload?.notes?.length ? (
            <div className="space-y-1 border-b border-border px-3 py-2">
              {payload.notes.map((n) => (
                <Notice key={n} tone="warn" icon={<CircleAlert className="size-3.5" />}>{n}</Notice>
              ))}
            </div>
          ) : null}
          {candles.isLoading ? (
            <div className="p-4"><Skeleton className="h-[540px] w-full" /></div>
          ) : candles.isError && !payload ? (
            <div className="space-y-3 p-6">
              <ErrorState
                title={`No ${pair} ${timeframe} candles to show`}
                error={candles.error}
                onRetry={() => void candles.refetch()}
              />
              {available.length > 0 && (
                <div className="flex flex-wrap items-center justify-center gap-2 text-xs text-muted">
                  Stored locally for {pair}:
                  {available.map((tf) => (
                    <Button key={tf} size="sm" onClick={() => setTimeframe(tf)}>{tf}</Button>
                  ))}
                </div>
              )}
            </div>
          ) : payload ? (
            <PriceChart
              payload={payload}
              toggles={toggles}
              liveCandle={liveCandle}
              markers={annotations.data?.markers}
              levels={annotations.data?.levels}
              height={chartHeight}
            />
          ) : null}
          {candles.isError && payload && (
            <div className="border-t border-border px-3 py-2 text-xs text-down">Latest refresh failed: {errorMessage(candles.error)} — showing the previous data.</div>
          )}
        </Card>

        <div className="space-y-4">
          <TickerCard pair={pair} exchange={exchange} ticker={ticker} lastClose={payload?.candles.at(-1)?.close ?? null} />
          {exchange === "binance" && <OrderBookCard pair={pair} />}
          <FeedCard />
        </div>
      </div>

      <DataPanel />

      <Dialog open={showToggles} onClose={() => setShowToggles(false)} title="Chart layers">
        <div className="grid grid-cols-2 gap-3">
          {(
            [
              ["volume", "Volume"],
              ["ema20", "EMA 20"],
              ["ema50", "EMA 50"],
              ["ema200", "EMA 200"],
              ["bb", "Bollinger Bands (20, 2)"],
              ["rsi", "RSI 14 panel"],
              ["atr", "ATR 14 panel"],
              ["markers", "Trade & signal markers"],
              ["levels", "Stop-loss / take-profit lines"],
            ] as [keyof ChartToggles, string][]
          ).map(([k, label]) => (
            <Switch key={k} checked={toggles[k]} onChange={(v) => setToggles((t) => ({ ...t, [k]: v }))} label={label} />
          ))}
        </div>
        <p className="mt-4 text-xs text-muted">
          Indicators are computed by the backend from closed candles only. Markers appear on the candle where an event was recorded; a signal is never drawn on a candle that had not closed when it was taken.
        </p>
      </Dialog>
    </div>
  );
}

function FreshnessBadge({ status, age }: { status: string; age: number | null }) {
  if (status === "fresh") return <Badge tone="up" dot>Fresh · last close {fmtAgo(age)}</Badge>;
  if (status === "empty") return <Badge tone="down" dot>No data</Badge>;
  return <Badge tone="warn" dot title="The newest stored candle is older than expected">Stale · last close {fmtAgo(age)}</Badge>;
}

function TickerCard({ pair, exchange, ticker, lastClose }: { pair: string; exchange: string; ticker: Ticker | null; lastClose: number | null }) {
  if (exchange !== "binance") {
    return (
      <Card title={pair} subtitle={`${exchange} · imported historical data`}>
        <div className="num text-2xl font-semibold">{fmtPrice(lastClose)}</div>
        <p className="mt-2 text-xs text-muted">Last close in the imported file. There is no live feed for this exchange.</p>
      </Card>
    );
  }
  const change = ticker?.change_pct_24h ?? null;
  return (
    <Card title={pair} subtitle="Binance spot · 24h" actions={ticker ? <Badge tone={ticker.stale ? "warn" : "up"} dot>{ticker.source === "binance-ws" ? "WebSocket" : "REST"} · {fmtAgo(ticker.age_s ?? null)}</Badge> : <Badge tone="down">No live price</Badge>}>
      {ticker ? (
        <div className="space-y-3">
          <div className="flex items-baseline justify-between gap-2">
            <div className="num text-2xl font-semibold">{fmtPrice(ticker.last)}</div>
            <div className={change === null ? "text-muted" : change >= 0 ? "num text-up" : "num text-down"}>{fmtPct(change, 2, true)}</div>
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            <Stat label="24h high" value={fmtPrice(ticker.high_24h)} />
            <Stat label="24h low" value={fmtPrice(ticker.low_24h)} />
            <Stat label="Bid" value={fmtPrice(ticker.bid)} />
            <Stat label="Ask" value={fmtPrice(ticker.ask)} />
            <Stat label="Spread" value={ticker.spread_pct === null ? "—" : `${fmtNum(ticker.spread_pct, 4)}%`} />
            <Stat label="24h volume" value={`${fmtCompact(ticker.quote_volume_24h)} ${pair.split("/")[1]}`} />
          </dl>
          <p className="text-[11px] text-faint">Updated {fmtDateTime(ticker.received_ms, true)}</p>
        </div>
      ) : (
        <EmptyState title="Live price unavailable" icon={<WifiOff className="size-5" />}>
          Binance has not been reached from this machine. Prices are never estimated; the chart shows stored candles with their age.
        </EmptyState>
      )}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-faint">{label}</dt>
      <dd className="num truncate text-text">{value}</dd>
    </div>
  );
}

function OrderBookCard({ pair }: { pair: string }) {
  const ob = useOrderBook(pair, true);
  const rows = 8;
  const maxQty = Math.max(1e-12, ...[...(ob.data?.bids ?? []), ...(ob.data?.asks ?? [])].slice(0, rows * 2).map((r) => r[1]));
  return (
    <Card title="Order book" subtitle={ob.data ? `Spread ${fmtNum(ob.data.spread_pct, 4)}% · REST snapshot` : "Top of book from Binance"}>
      {ob.isError ? (
        <p className="text-xs leading-5 text-muted">Unavailable: {errorMessage(ob.error)}</p>
      ) : !ob.data ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <div className="num space-y-px text-[11px]">
          {[...ob.data.asks.slice(0, rows)].reverse().map(([p, q]) => (
            <BookRow key={`a${p}`} price={p} qty={q} max={maxQty} side="ask" />
          ))}
          <div className="py-1 text-center text-xs text-muted">{fmtPrice(ob.data.best_bid)} / {fmtPrice(ob.data.best_ask)}</div>
          {ob.data.bids.slice(0, rows).map(([p, q]) => (
            <BookRow key={`b${p}`} price={p} qty={q} max={maxQty} side="bid" />
          ))}
        </div>
      )}
    </Card>
  );
}

function BookRow({ price, qty, max, side }: { price: number; qty: number; max: number; side: "bid" | "ask" }) {
  return (
    <div className="relative flex justify-between px-1 py-0.5">
      <div className={side === "bid" ? "absolute inset-y-0 right-0 bg-up/10" : "absolute inset-y-0 right-0 bg-down/10"} style={{ width: `${(qty / max) * 100}%` }} />
      <span className={side === "bid" ? "relative text-up" : "relative text-down"}>{fmtPrice(price)}</span>
      <span className="relative text-muted">{fmtNum(qty, 4)}</span>
    </div>
  );
}

function FeedCard() {
  const status = useMarketStatus();
  const s = status.data;
  return (
    <Card title="Market data feed" subtitle="Connection and candle freshness">
      {!s ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        <div className="space-y-3 text-xs">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-muted">{s.websocket.connected ? <Wifi className="size-3.5 text-up" /> : <WifiOff className="size-3.5 text-down" />} WebSocket</span>
            <span>{s.websocket.connected ? `connected · ${s.websocket.streams} streams` : "disconnected"}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">REST API</span>
            <span>{s.rest.reachable === null ? "not tried yet" : s.rest.reachable ? `ok · ${fmtNum(s.rest.latency_ms, 0)} ms` : "unreachable"}</span>
          </div>
          {(s.rest.last_error || s.websocket.last_error) && !s.rest.reachable && (
            <p className="break-words rounded-md bg-down/5 px-2 py-1.5 text-[11px] leading-4 text-down/90">
              {s.rest.last_error ?? s.websocket.last_error}
              {s.rest.cooldown_s > 0 ? ` · retry in ${Math.round(s.rest.cooldown_s)}s` : ""}
            </p>
          )}
          <div className="space-y-1 border-t border-border pt-2">
            {s.series.map((f) => (
              <div key={`${f.pair}${f.timeframe}`} className="flex items-center justify-between gap-2">
                <span className="text-muted">{f.pair} {f.timeframe}</span>
                <Badge tone={f.status === "fresh" ? "up" : f.status === "empty" ? "down" : "warn"}>
                  {f.status === "empty" ? "no data" : `${f.status} · ${fmtAgo(f.age_s)}`}
                </Badge>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function DataPanel() {
  const inv = useInventory();
  const jobs = useJobs();
  const pairs = usePairs();
  const toast = useToast();
  const qc = useQueryClient();
  const [download, setDownload] = useState(false);
  const [upload, setUpload] = useState(false);
  const [busy, setBusy] = useState(false);
  const active = (jobs.data ?? []).filter((j) => j.kind === "download" && (j.status === "running" || j.status === "queued"));
  const recent = (jobs.data ?? []).filter((j) => j.kind === "download").slice(0, 3);

  const importSamples = async () => {
    setBusy(true);
    try {
      const r = await post<{ results: { source?: string; written?: number; error?: string }[] }>("/api/data/import-samples");
      const ok = r.results.filter((x) => !x.error);
      toast("success", `Imported ${ok.length} sample file(s)`, ok.map((x) => `${x.source}: ${x.written} candles`).join(" · "));
      await qc.invalidateQueries({ queryKey: ["inventory"] });
      await qc.invalidateQueries({ queryKey: ["pairs"] });
    } catch (e) {
      toast("error", "Import failed", errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Historical data"
      icon={<Database className="size-4" />}
      subtitle="Stored candles by exchange, pair and timeframe. Every candle keeps the label of where it came from."
      actions={
        <>
          <Button size="sm" icon={<Download className="size-3.5" />} onClick={() => setDownload(true)}>Download from Binance</Button>
          <Button size="sm" icon={<Upload className="size-3.5" />} onClick={() => setUpload(true)}>Import CSV</Button>
          <Button size="sm" variant="ghost" icon={<FileDown className="size-3.5" />} loading={busy} onClick={importSamples}>Import bundled samples</Button>
        </>
      }
    >
      {active.length > 0 && <div className="mb-3 space-y-2">{active.map((j) => <JobProgress key={j.id} job={j} />)}</div>}
      {inv.data?.missing_for_strategies.length ? (
        <Notice tone="warn" icon={<CircleAlert className="size-3.5" />} className="mb-3">
          Strategies need Binance candles for {inv.data.missing_for_strategies.map((m) => `${m.pair} ${m.timeframe}`).join(", ")}. Use “Download from Binance” on a machine that can reach Binance.
        </Notice>
      ) : null}
      <DataTable<InventoryRow>
        rows={inv.data?.series}
        loading={inv.isLoading}
        rowKey={(r) => `${r.exchange}${r.pair}${r.timeframe}`}
        empty={<EmptyState title="No candles stored yet" icon={<Database className="size-5" />}>Download history from Binance, import a CSV export, or import the real sample files bundled with the project.</EmptyState>}
        columns={[
          { key: "exchange", header: "Exchange", render: (r) => <Badge tone={r.exchange === "binance" ? "info" : "neutral"}>{r.exchange}</Badge>, sort: (r) => r.exchange },
          { key: "pair", header: "Pair", render: (r) => r.pair, sort: (r) => r.pair },
          { key: "tf", header: "TF", render: (r) => r.timeframe },
          { key: "candles", header: "Candles", align: "right", render: (r) => fmtNum(r.candles, 0), sort: (r) => r.candles },
          { key: "range", header: "Range (UTC open times)", render: (r) => `${fmtDateTime(r.first_ts)} → ${fmtDateTime(r.last_ts)}` },
          { key: "coverage", header: "Coverage", align: "right", render: (r) => <span className={r.coverage_pct < 99 ? "text-warn" : ""}>{fmtNum(r.coverage_pct, 2)}%</span>, sort: (r) => r.coverage_pct },
          { key: "gaps", header: "Gaps", align: "right", render: (r) => (r.missing ? `${r.missing} in ${r.gap_count}` : "none") },
          { key: "sources", header: "Sources", render: (r) => <span className="text-xs text-muted">{Object.keys(r.sources).join(", ")}</span> },
        ]}
      />
      {recent.some((j) => j.status === "failed" || j.status === "completed") && (
        <div className="mt-3 space-y-1">
          {recent.filter((j) => j.status !== "running" && j.status !== "queued").map((j) => <JobResult key={j.id} job={j} />)}
        </div>
      )}
      <DownloadDialog open={download} onClose={() => setDownload(false)} pairs={pairs.data?.pairs ?? []} />
      <UploadDialog open={upload} onClose={() => setUpload(false)} />
    </Card>
  );
}

function JobProgress({ job }: { job: Job }) {
  return (
    <div className="rounded-lg border border-border bg-surface-2 px-3 py-2">
      <div className="mb-1 flex justify-between text-xs"><span>{job.label}</span><span className="num text-muted">{Math.round(job.progress * 100)}%</span></div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-3"><div className="h-full bg-accent transition-all" style={{ width: `${job.progress * 100}%` }} /></div>
      {job.message && <div className="mt-1 text-[11px] text-muted">{job.message}</div>}
    </div>
  );
}

function JobResult({ job }: { job: Job }) {
  const results = Array.isArray(job.result) ? (job.result as { pair: string; timeframe: string; written?: number; error?: string }[]) : [];
  return (
    <div className="text-xs text-muted">
      <span className={job.status === "failed" ? "text-down" : "text-text"}>{job.label}</span> · {fmtDateTime(job.finished_at)} ·{" "}
      {job.error ?? results.map((r) => `${r.pair} ${r.timeframe}: ${r.error ? `failed (${r.error})` : `+${r.written}`}`).join(" · ")}
    </div>
  );
}

function DownloadDialog({ open, onClose, pairs }: { open: boolean; onClose: () => void; pairs: string[] }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string[]>(["BTC/USDT", "ETH/USDT"]);
  const [tfs, setTfs] = useState<string[]>(["15m", "1h", "4h"]);
  const [days, setDays] = useState(400);
  const submit = async () => {
    try {
      await post("/api/data/sync", { pairs: selected, timeframes: tfs, days });
      toast("info", "Download started", "Progress appears above the table. Already stored candles are skipped.");
      await qc.invalidateQueries({ queryKey: ["jobs"] });
      onClose();
    } catch (e) {
      toast("error", "Could not start the download", errorMessage(e));
    }
  };
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  return (
    <Dialog open={open} onClose={onClose} title="Download history from Binance" footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={submit} disabled={!selected.length || !tfs.length}>Start download</Button></>}>
      <div className="space-y-4">
        <Field label="Pairs">
          <div className="flex flex-wrap gap-2">{pairs.map((p) => <Switch key={p} checked={selected.includes(p)} onChange={() => setSelected((s) => toggle(s, p))} label={p} />)}</div>
        </Field>
        <Field label="Timeframes">
          <div className="flex flex-wrap gap-2">{TIMEFRAMES.map((t) => <Switch key={t} checked={tfs.includes(t)} onChange={() => setTfs((s) => toggle(s, t))} label={t} />)}</div>
        </Field>
        <Field label="Days of history" hint="1m candles for 400 days are about 576,000 rows per pair; start with 15m, 1h and 4h.">
          <Input type="number" min={1} max={3650} value={days} onChange={(e) => setDays(Number(e.target.value))} />
        </Field>
        <p className="text-xs text-muted">Public market data only: no API key is used. Requests respect Binance rate limits and resume where a previous download stopped.</p>
      </div>
    </Dialog>
  );
}

function UploadDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [exchange, setExchange] = useState("binance");
  const [pair, setPair] = useState("BTC/USDT");
  const [timeframe, setTf] = useState("1h");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!file) return;
    setBusy(true);
    const form = new FormData();
    form.append("file", file);
    form.append("exchange", exchange);
    form.append("pair", pair);
    form.append("timeframe", timeframe);
    try {
      const r = await api<{ written: number; report: { issues: string[] } }>("/api/data/import", { method: "POST", body: form });
      toast("success", `Imported ${r.written} candles`, r.report.issues.join(" · ") || "No issues found");
      await qc.invalidateQueries({ queryKey: ["inventory"] });
      await qc.invalidateQueries({ queryKey: ["pairs"] });
      onClose();
    } catch (e) {
      toast("error", "Import failed", errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onClose={onClose} title="Import candles from a CSV file" footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!file} onClick={submit}>Import</Button></>}>
      <div className="space-y-3">
        <Field label="CSV file" hint="Header with ts/timestamp/open_time/date, open, high, low, close, volume — or a Binance kline archive (no header).">
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="block w-full text-xs text-muted file:mr-3 file:rounded-md file:border-0 file:bg-surface-3 file:px-3 file:py-1.5 file:text-text" />
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Exchange (where the data really comes from)">
            <Select value={exchange} onChange={(e) => setExchange(e.target.value)} options={["binance", "coinbase", "kraken", "bitstamp", "bybit", "okx", "other"].map((x) => ({ value: x, label: x }))} />
          </Field>
          <Field label="Pair"><Input value={pair} onChange={(e) => setPair(e.target.value.toUpperCase())} /></Field>
          <Field label="Timeframe"><Select value={timeframe} onChange={(e) => setTf(e.target.value)} options={TIMEFRAMES.map((t) => ({ value: t, label: t }))} /></Field>
        </div>
        <p className="text-xs text-muted">Rows are validated exactly like downloads. Invalid rows are removed and counted; gaps are reported and never filled.</p>
      </div>
    </Dialog>
  );
}
