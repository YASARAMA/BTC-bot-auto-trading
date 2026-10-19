// Portfolio and history: positions, orders and every completed trade, with filters and CSV export.
import { useMemo, useState } from "react";
import { FileDown, ListFilter, RotateCcw } from "lucide-react";
import { OrderStatusBadge, RegimeBadge, SideBadge, regimeLabel } from "../components/domain/Badges";
import { ENGINE_KEYS } from "../components/domain/Controls";
import { RunPicker, parseRun } from "../components/domain/RunPicker";
import { TradesTable } from "../components/domain/Tables";
import { SimulatedTag } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState } from "../components/ui/Feedback";
import { Field, Input, Segmented, Select, Tabs } from "../components/ui/Form";
import { Kpi } from "../components/ui/Kpi";
import { DataTable } from "../components/ui/Table";
import { qs } from "../lib/api";
import { fmtDateTime, fmtNum, fmtPct, fmtPrice, fmtSignedUsd, fmtUsd, titleCase, toneOf } from "../lib/format";
import { useOrders, usePaperStatus, usePositions, useTrades, type TradeFilters } from "../lib/queries";
import { useLiveRefresh } from "../lib/useLiveRefresh";
import { usePersistent } from "../lib/usePersistent";

export function PortfolioPage() {
  const status = usePaperStatus();
  const [tab, setTab] = usePersistent<"trades" | "positions" | "orders">("portfolio.tab", "trades");
  useLiveRefresh("paper", ENGINE_KEYS, 3000);
  const pf = status.data?.portfolio;
  const s = status.data?.session;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Equity" value={pf ? fmtUsd(pf.equity) : "—"} loading={status.isLoading} badge={<SimulatedTag />} sub={s ? `session ${s.id}` : "no active session"} />
        <Kpi label="Cash" value={pf ? fmtUsd(pf.cash) : "—"} loading={status.isLoading} />
        <Kpi label="Realized PnL" value={pf ? fmtSignedUsd(pf.realized_pnl) : "—"} tone={toneOf(pf?.realized_pnl)} loading={status.isLoading} />
        <Kpi label="Unrealized PnL" value={pf ? fmtSignedUsd(pf.unrealized_pnl) : "—"} tone={toneOf(pf?.unrealized_pnl)} loading={status.isLoading} />
        <Kpi label="Fees paid" value={pf ? fmtUsd(pf.fees_paid) : "—"} loading={status.isLoading} />
        <Kpi label="Exposure" value={pf ? fmtPct(pf.exposure_pct, 1) : "—"} sub={pf ? fmtUsd(pf.exposure) : undefined} loading={status.isLoading} />
      </div>
      <Card bodyClassName="p-0">
        <div className="px-4 pt-2">
          <Tabs
            value={tab}
            onChange={setTab}
            tabs={[
              { value: "trades", label: "Trade history" },
              { value: "positions", label: "Positions" },
              { value: "orders", label: "Orders" },
            ]}
          />
        </div>
        <div className="p-4">
          {tab === "trades" && <TradeHistory />}
          {tab === "positions" && <Positions />}
          {tab === "orders" && <Orders />}
        </div>
      </Card>
    </div>
  );
}

function TradeHistory() {
  const [run, setRun] = usePersistent("portfolio.run", "");
  const [scenario, setScenario] = useState("base");
  const [pair, setPair] = useState("");
  const [strategy, setStrategy] = useState("");
  const [regime, setRegime] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const r = parseRun(run);
  const filters: TradeFilters = {
    ...r,
    scenario: r.source === "backtest" ? scenario : undefined,
    pair: pair || undefined,
    strategy: strategy || undefined,
    regime: regime || undefined,
    start: start || undefined,
    end: end ? `${end}T23:59:59Z` : undefined,
  };
  const trades = useTrades(filters, 5000);
  const all = trades.data ?? [];
  const opts = useMemo(
    () => ({
      pairs: Array.from(new Set(all.map((t) => t.pair).concat(pair ? [pair] : []))).sort(),
      strategies: Array.from(new Set(all.map((t) => t.strategy).concat(strategy ? [strategy] : []))).sort(),
      regimes: Array.from(new Set(all.map((t) => t.regime).concat(regime ? [regime] : []))).sort(),
    }),
    [all, pair, strategy, regime],
  );
  const net = all.reduce((a, t) => a + t.net_pnl, 0);
  const fees = all.reduce((a, t) => a + t.fees, 0);
  const wins = all.filter((t) => t.net_pnl > 0).length;
  const reset = () => {
    setPair("");
    setStrategy("");
    setRegime("");
    setStart("");
    setEnd("");
  };
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <RunPicker value={run} onChange={setRun} scenario={scenario} onScenario={setScenario} />
        <Field label="Pair" className="w-36">
          <Select value={pair} onChange={(e) => setPair(e.target.value)} options={[{ value: "", label: "All" }, ...opts.pairs.map((p) => ({ value: p, label: p }))]} />
        </Field>
        <Field label="Strategy" className="w-44">
          <Select value={strategy} onChange={(e) => setStrategy(e.target.value)} options={[{ value: "", label: "All" }, ...opts.strategies.map((p) => ({ value: p, label: titleCase(p) }))]} />
        </Field>
        <Field label="Regime at entry" className="w-44">
          <Select value={regime} onChange={(e) => setRegime(e.target.value)} options={[{ value: "", label: "All" }, ...opts.regimes.map((p) => ({ value: p, label: regimeLabel(p) }))]} />
        </Field>
        <Field label="Closed from" className="w-40">
          <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="Closed to" className="w-40">
          <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Button variant="ghost" icon={<RotateCcw className="size-3.5" />} onClick={reset}>
          Reset
        </Button>
        <a href={`/api/portfolio/trades.csv${qs({ ...filters })}`} download className="ml-auto">
          <Button icon={<FileDown className="size-4" />}>Export CSV</Button>
        </a>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted">
        <ListFilter className="size-3.5" />
        <span>
          <b className="num text-text">{all.length}</b> trades
        </span>
        <span>
          win rate <b className="num text-text">{all.length ? fmtPct((wins / all.length) * 100, 1) : "—"}</b>
        </span>
        <span>
          net <b className={`num ${net >= 0 ? "text-up" : "text-down"}`}>{fmtSignedUsd(net)}</b>
        </span>
        <span>
          fees <b className="num text-text">{fmtUsd(fees)}</b>
        </span>
        {r.source === "backtest" && <span className="text-warn">Backtest trades: simulated on historical data.</span>}
      </div>
      {trades.isError ? (
        <ErrorState error={trades.error} onRetry={() => trades.refetch()} />
      ) : (
        <TradesTable trades={trades.data} loading={trades.isLoading} maxHeight={560} showRun={!!r.run_id} empty="No trades match these filters" />
      )}
    </div>
  );
}

function Positions() {
  const [status, setStatus] = useState<"open" | "closed" | "all">("open");
  const q = usePositions(status);
  return (
    <div className="space-y-3">
      <Segmented value={status} onChange={setStatus} options={[{ value: "open", label: "Open" }, { value: "closed", label: "Closed" }, { value: "all", label: "All" }]} />
      <DataTable
        rows={q.data}
        loading={q.isLoading}
        rowKey={(p) => p.id}
        dense
        maxHeight={560}
        empty={<EmptyState title={status === "open" ? "No open positions in the current session" : "No positions"} />}
        columns={[
          { key: "pair", header: "Pair", render: (p) => <span className="font-medium">{p.pair}</span>, sort: (p) => p.pair },
          { key: "status", header: "Status", render: (p) => p.status },
          { key: "strategy", header: "Strategy", render: (p) => titleCase(p.strategy) },
          { key: "regime", header: "Regime", render: (p) => <RegimeBadge regime={p.regime} /> },
          { key: "qty", header: "Quantity", align: "right", render: (p) => fmtNum(p.qty - p.qty_exited, 6) },
          { key: "entry", header: "Entry", align: "right", render: (p) => fmtPrice(p.entry_price) },
          { key: "stop", header: "Stop", align: "right", render: (p) => <span className="text-down">{fmtPrice(p.stop_price)}</span> },
          { key: "tp", header: "Target", align: "right", render: (p) => <span className="text-up">{fmtPrice(p.take_profit)}</span> },
          { key: "mark", header: "Mark", align: "right", render: (p) => fmtPrice(p.mark ?? null) },
          {
            key: "upnl",
            header: "Unrealized",
            align: "right",
            render: (p) => (p.unrealized_pnl === undefined ? "—" : <span className={p.unrealized_pnl >= 0 ? "text-up" : "text-down"}>{fmtSignedUsd(p.unrealized_pnl)} ({fmtPct(p.unrealized_pct, 2, true)})</span>),
            sort: (p) => p.unrealized_pnl ?? null,
          },
          { key: "risk", header: "Initial risk", align: "right", render: (p) => fmtUsd(p.initial_risk) },
          { key: "opened", header: "Opened", render: (p) => fmtDateTime(p.entry_ts), sort: (p) => p.entry_ts },
          { key: "closed", header: "Closed", render: (p) => fmtDateTime(p.closed_ts) },
          { key: "exit", header: "Exit reason", render: (p) => <span className="block max-w-[240px] truncate text-muted" title={p.exit_reason ?? ""}>{p.exit_reason ?? ""}</span> },
        ]}
      />
    </div>
  );
}

function Orders() {
  const [status, setStatus] = useState("");
  const q = useOrders(status || undefined);
  return (
    <div className="space-y-3">
      <Segmented
        value={status}
        onChange={setStatus}
        options={[
          { value: "", label: "All" },
          { value: "filled", label: "Filled" },
          { value: "pending", label: "Pending" },
          { value: "partially_filled", label: "Partial" },
          { value: "cancelled", label: "Cancelled" },
          { value: "rejected", label: "Rejected" },
        ]}
      />
      <DataTable
        rows={q.data}
        loading={q.isLoading}
        rowKey={(o) => o.id}
        dense
        maxHeight={560}
        empty={<EmptyState title="No orders in the current session" />}
        columns={[
          { key: "created", header: "Decided", render: (o) => fmtDateTime(o.created_ts), sort: (o) => o.created_ts },
          { key: "filled", header: "Filled", render: (o) => fmtDateTime(o.filled_ts) },
          { key: "pair", header: "Pair", render: (o) => o.pair },
          { key: "side", header: "Side", render: (o) => <SideBadge side={o.side} /> },
          { key: "purpose", header: "Purpose", render: (o) => o.purpose },
          { key: "status", header: "Status", render: (o) => <OrderStatusBadge status={o.status} /> },
          { key: "qty", header: "Filled / requested", align: "right", render: (o) => `${fmtNum(o.filled_qty, 6)} / ${fmtNum(o.requested_qty, 6)}` },
          { key: "ref", header: "Decision close", align: "right", render: (o) => fmtPrice(o.ref_price) },
          { key: "fill", header: "Fill price", align: "right", render: (o) => fmtPrice(o.fill_price) },
          { key: "fee", header: "Fee", align: "right", render: (o) => fmtUsd(o.fee) },
          { key: "slip", header: "Slippage", align: "right", render: (o) => fmtUsd(o.slippage_cost) },
          { key: "id", header: "Client order id", render: (o) => <span className="text-faint">{o.client_order_id}</span> },
          { key: "why", header: "Detail", render: (o) => <span className="block max-w-[280px] truncate text-muted" title={o.reject_reason ?? o.exit_reason ?? o.reason ?? ""}>{o.reject_reason ?? o.exit_reason ?? o.reason ?? ""}</span> },
        ]}
      />
      <p className="text-[11px] text-faint">Simulated orders. Each carries a deterministic client order id so a restart can never create the same order twice.</p>
    </div>
  );
}
