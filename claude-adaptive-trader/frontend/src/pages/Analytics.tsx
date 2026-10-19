// Performance analytics from stored results: a paper session or a backtest experiment, with
// filters by pair, strategy, regime, timeframe and period. Nothing here is estimated or invented;
// empty sections say so.
import { useState } from "react";
import { ListFilter, RotateCcw } from "lucide-react";
import { DrawdownChart, EquityChart, Histogram, MonthlyHeatmap, SeriesArea, SignedBars } from "../components/charts/Charts";
import { regimeLabel } from "../components/domain/Badges";
import { RunPicker, parseRun } from "../components/domain/RunPicker";
import { GroupTable, MetricsList } from "../components/domain/Tables";
import { Badge, SimulatedTag } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState, Notice, SkeletonRows } from "../components/ui/Feedback";
import { Field, Input, Select } from "../components/ui/Form";
import { Kpi } from "../components/ui/Kpi";
import { DataTable } from "../components/ui/Table";
import { fmtNum, fmtPct, fmtSignedUsd, fmtUsd, titleCase, toneOf } from "../lib/format";
import { useAnalytics, type TradeFilters } from "../lib/queries";
import type { Analytics } from "../lib/types";
import { usePersistent } from "../lib/usePersistent";

export function AnalyticsPage() {
  const [run, setRun] = usePersistent("analytics.run", "");
  const [scenario, setScenario] = useState("base");
  const [pair, setPair] = useState("");
  const [strategy, setStrategy] = useState("");
  const [regime, setRegime] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const r = parseRun(run);
  const params: TradeFilters = {
    source: r.source ?? "paper",
    run_id: r.run_id,
    scenario: r.source === "backtest" ? scenario : "base",
    pair: pair || undefined,
    strategy: strategy || undefined,
    regime: regime || undefined,
    timeframe: timeframe || undefined,
    start: start || undefined,
    end: end ? `${end}T23:59:59Z` : undefined,
  };
  const q = useAnalytics(params);
  const a = q.data;
  const o = a?.options;
  const reset = () => {
    setPair("");
    setStrategy("");
    setRegime("");
    setTimeframe("");
    setStart("");
    setEnd("");
  };

  return (
    <div className="space-y-4">
      <Card bodyClassName="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <RunPicker value={run} onChange={(v) => { setRun(v); setScenario("base"); }} scenario={scenario} onScenario={setScenario} />
          <Field label="Pair" className="w-36">
            <Select value={pair} onChange={(e) => setPair(e.target.value)} options={[{ value: "", label: "All" }, ...(o?.pairs ?? []).map((p) => ({ value: p, label: p }))]} />
          </Field>
          <Field label="Strategy" className="w-44">
            <Select value={strategy} onChange={(e) => setStrategy(e.target.value)} options={[{ value: "", label: "All" }, ...(o?.strategies ?? []).map((p) => ({ value: p, label: titleCase(p) }))]} />
          </Field>
          <Field label="Regime" className="w-44">
            <Select value={regime} onChange={(e) => setRegime(e.target.value)} options={[{ value: "", label: "All" }, ...(o?.regimes ?? []).map((p) => ({ value: p, label: regimeLabel(p) }))]} />
          </Field>
          <Field label="Timeframe" className="w-28">
            <Select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} options={[{ value: "", label: "All" }, ...(o?.timeframes ?? []).map((p) => ({ value: p, label: p }))]} />
          </Field>
          <Field label="From" className="w-40">
            <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </Field>
          <Field label="To" className="w-40">
            <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </Field>
          <Button variant="ghost" icon={<RotateCcw className="size-3.5" />} onClick={reset}>
            Reset filters
          </Button>
        </div>
      </Card>

      {q.isError && !a ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !a ? (
        <SkeletonRows rows={10} />
      ) : a.empty || !a.metrics ? (
        <EmptyState title={a.message ?? "No results recorded for this run yet"} icon={<ListFilter className="size-6" />}>
          Analytics are computed from stored trades and equity only. Start a paper session or run a backtest, then choose it above.
        </EmptyState>
      ) : (
        <AnalyticsBody a={a} />
      )}
    </div>
  );
}

function AnalyticsBody({ a }: { a: Analytics }) {
  const m = a.metrics!;
  const total = (a.wins ?? 0) + (a.losses ?? 0);
  const fees = a.fee_contribution;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <Badge tone={a.source === "paper" ? "accent" : "warn"}>{a.source === "paper" ? "Paper session" : "Backtest (historical simulation)"}</Badge>
        <span>{a.run_id}</span>
        {a.source === "paper" && <SimulatedTag />}
      </div>
      {a.equity_note && <Notice tone="warn">{a.equity_note}</Notice>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi label="Trades" value={String(m.trades)} />
        <Kpi label="Net return" value={fmtPct(m.net_return_pct, 2, true)} tone={toneOf(m.net_return_pct)} />
        <Kpi label="Win rate" value={fmtPct(m.win_rate_pct, 1)} />
        <Kpi label="Profit factor" value={fmtNum(m.profit_factor, 2)} />
        <Kpi label="Expectancy" value={m.expectancy_r === null ? "—" : `${fmtNum(m.expectancy_r, 3)} R`} tone={toneOf(m.expectancy_r)} />
        <Kpi label="Max drawdown" value={fmtPct(m.max_drawdown_pct, 2)} />
        <Kpi label="Sharpe" value={m.sharpe === null ? "n/a" : fmtNum(m.sharpe, 2)} hint="Daily returns, annualised; only with 30+ daily returns." />
        <Kpi label="Sortino" value={m.sortino === null ? "n/a" : fmtNum(m.sortino, 2)} />
        <Kpi label="Buy & hold" value={fmtPct(m.benchmark_return_pct, 2, true)} tone={toneOf(m.benchmark_return_pct)} />
        <Kpi label="Excess" value={fmtPct(m.excess_return_pct, 2, true)} tone={toneOf(m.excess_return_pct)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Equity" subtitle={a.benchmark ? "Solid: strategy · dashed: equal-weight buy & hold with the same costs" : "Strategy equity"}>
          <EquityChart points={a.equity} initial={m.initial_cash} height={260} />
        </Card>
        <Card title="Drawdown" subtitle="Fall from the running peak of equity">
          <DrawdownChart points={a.drawdown} height={260} />
        </Card>
      </div>

      <Card title="Monthly returns" subtitle="Compounded from month-end equity; the last column compounds each year">
        <MonthlyHeatmap returns={a.monthly_returns} />
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Daily PnL" subtitle="Net PnL of trades closed each UTC day" className="xl:col-span-2">
          <SignedBars data={a.daily_pnl} xKey="day" yKey="pnl" label="Daily PnL" height={220} />
        </Card>
        <Card title="Wins and losses">
          {total ? (
            <div className="space-y-3 text-xs">
              <div className="flex h-4 overflow-hidden rounded-full">
                <div className="bg-up" style={{ width: `${((a.wins ?? 0) / total) * 100}%` }} />
                <div className="bg-down" style={{ width: `${((a.losses ?? 0) / total) * 100}%` }} />
              </div>
              <div className="flex justify-between">
                <span className="text-up">{a.wins} wins ({fmtPct(((a.wins ?? 0) / total) * 100, 1)})</span>
                <span className="text-down">{a.losses} losses / flat</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-muted">
                <span>Average win <b className="num block text-up">{fmtSignedUsd(m.avg_win)}</b></span>
                <span>Average loss <b className="num block text-down">{fmtSignedUsd(m.avg_loss)}</b></span>
                <span>Avg win (R) <b className="num block text-text">{fmtNum(m.avg_win_r, 2)}</b></span>
                <span>Avg loss (R) <b className="num block text-text">{fmtNum(m.avg_loss_r, 2)}</b></span>
                <span>Longest win streak <b className="num block text-text">{m.longest_winning_streak}</b></span>
                <span>Longest loss streak <b className="num block text-text">{m.longest_losing_streak}</b></span>
              </div>
            </div>
          ) : (
            <EmptyState title="No completed trades" compact />
          )}
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Distribution of R multiples" subtitle="Net result of each trade in units of its initial risk">
          <Histogram bins={a.r_distribution} label="R multiples" unit="R" />
        </Card>
        <Card title="Distribution of trade PnL" subtitle="Net of fees and slippage">
          <Histogram bins={a.pnl_distribution} label="trade PnL" />
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Strategy comparison">
          <GroupTable groups={a.by_strategy} label="Strategy" />
        </Card>
        <Card title="Performance by regime at entry">
          <GroupTable groups={a.by_regime} label="Regime" format={regimeLabel} />
        </Card>
        <Card title="By pair">
          <GroupTable groups={a.by_pair} label="Pair" format={(k) => k} />
        </Card>
        <Card title="By exit reason">
          <GroupTable groups={a.by_exit_reason} label="Exit" format={(k) => k} />
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Fee contribution" subtitle="How much of the gross result trading costs consumed">
          {fees ? (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div className="rounded-lg border border-border p-2">
                  <div className="text-muted">Gross PnL (before fees)</div>
                  <div className={`num text-sm font-semibold ${fees.gross_pnl >= 0 ? "text-up" : "text-down"}`}>{fmtSignedUsd(fees.gross_pnl)}</div>
                </div>
                <div className="rounded-lg border border-border p-2">
                  <div className="text-muted">Fees</div>
                  <div className="num text-sm font-semibold text-down">−{fmtUsd(fees.fees)}</div>
                </div>
                <div className="rounded-lg border border-border p-2">
                  <div className="text-muted">Net PnL</div>
                  <div className={`num text-sm font-semibold ${fees.net_pnl >= 0 ? "text-up" : "text-down"}`}>{fmtSignedUsd(fees.net_pnl)}</div>
                </div>
              </div>
              <p className="text-[11px] text-faint">Slippage and spread are already inside the fill prices, so they are part of the gross PnL; their estimated total was {fmtUsd(m.slippage_estimate)}.</p>
              <SignedBars data={Object.entries(fees.fees_by_month).map(([month, f]) => ({ month, fees: -f }))} xKey="month" yKey="fees" label="Fees" height={150} />
            </div>
          ) : (
            <EmptyState title="No fees recorded" compact />
          )}
        </Card>
        <Card title="Exposure over time" subtitle="Share of equity held in positions">
          <SeriesArea points={a.exposure as unknown as Record<string, number>[]} dataKey="exposure_pct" label="Exposure" height={250} />
        </Card>
      </div>

      {(a.sensitivity || a.oos_vs_is) && (
        <div className="grid gap-4 xl:grid-cols-2">
          {a.oos_vs_is && (
            <Card title="In-sample vs out-of-sample" subtitle="Development segment against walk-forward and validation results">
              <DataTable
                rows={Object.entries(a.oos_vs_is).map(([k, v]) => ({ k, v: v ?? {} }))}
                rowKey={(r) => r.k}
                dense
                columns={[
                  { key: "k", header: "Sample", render: (r) => titleCase(r.k) },
                  { key: "t", header: "Trades", align: "right", render: (r) => String(r.v.trades ?? "—") },
                  { key: "e", header: "Expectancy (R)", align: "right", render: (r) => fmtNum(r.v.expectancy_r ?? null, 3) },
                  { key: "ret", header: "Net return", align: "right", render: (r) => fmtPct(r.v.net_return_pct ?? null, 2, true) },
                  { key: "pf", header: "PF", align: "right", render: (r) => fmtNum(r.v.profit_factor ?? null, 2) },
                ]}
              />
              <p className="mt-2 text-[11px] text-faint">A large drop from in-sample to out-of-sample is the usual sign of overfitting.</p>
            </Card>
          )}
          {a.sensitivity && (
            <Card title="Parameter sensitivity" subtitle={`Stability ${fmtNum(a.sensitivity.stability, 2)}: share of neighbouring parameter sets with positive expectancy`}>
              <DataTable
                rows={a.sensitivity.neighbours}
                rowKey={(r, i) => `${r.param}-${i}`}
                dense
                columns={[
                  { key: "p", header: "Parameter", render: (r) => titleCase(r.param) },
                  { key: "v", header: "Value (base)", render: (r) => `${String(r.value)} (${String(r.base_value)})` },
                  { key: "t", header: "Trades", align: "right", render: (r) => r.trades },
                  { key: "e", header: "Expectancy (R)", align: "right", render: (r) => fmtNum(r.expectancy_r, 3) },
                ]}
              />
            </Card>
          )}
        </div>
      )}

      <Card title="All metrics" subtitle="For the filtered trades; equity-based figures cover the whole run">
        <MetricsList m={m} />
      </Card>
    </>
  );
}
