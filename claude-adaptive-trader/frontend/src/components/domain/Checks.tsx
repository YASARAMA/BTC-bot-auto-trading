import { CircleCheck, CircleX } from "lucide-react";
import { cn } from "../../lib/cn";
import { fmtNum, fmtPrice, fmtUsd, titleCase } from "../../lib/format";
import type { CheckResult, Sizing } from "../../lib/types";

export const CHECK_HELP: Record<string, string> = {
  market_data_freshness: "The latest closed candle is recent enough to act on.",
  candle_completeness: "No missing candles in the window the signal was computed on.",
  strategy_eligibility: "The strategy is allowed to trade in the current regime.",
  signal_validity: "Entry, stop and target are consistent and the confidence is sufficient.",
  stop_loss_validity: "The stop is below entry and within the minimum and maximum distance.",
  position_size: "Size = risk budget / stop distance, limited by cash, liquidity and exposure caps.",
  expected_transaction_costs: "Fees, slippage and spread are small relative to the planned reward.",
  available_virtual_balance: "Enough simulated cash for the order and its fees.",
  existing_positions: "Position count and per-pair limits are respected.",
  correlated_exposure: "Not too many positions in correlated pairs.",
  daily_loss_limit: "The daily loss circuit breaker is not tripped.",
  drawdown_limit: "The drawdown circuit breaker is not tripped.",
  duplicate_order_prevention: "This signal has not already produced an order.",
  execution_engine_health: "The paper engine and its data feed are healthy.",
};

export function ChecksList({ checks, compact }: { checks: CheckResult[]; compact?: boolean }) {
  return (
    <ol className={cn("grid gap-1", !compact && "sm:grid-cols-2")}>
      {checks.map((c, i) => (
        <li key={c.name} className={cn("flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs", c.passed ? "border-border bg-surface-2/40" : "border-down/40 bg-down/5")}>
          {c.passed ? <CircleCheck className="mt-0.5 size-3.5 shrink-0 text-up" /> : <CircleX className="mt-0.5 size-3.5 shrink-0 text-down" />}
          <div className="min-w-0">
            <div className="font-medium text-text">
              <span className="num mr-1 text-faint">{i + 1}.</span>
              {titleCase(c.name)}
            </div>
            {c.detail && <div className="break-words text-muted">{c.detail}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function SizingDetail({ sizing }: { sizing: Sizing }) {
  return (
    <div className="rounded-md border border-border bg-surface-2/40 p-2 text-xs">
      <div className="mb-1 font-medium text-text">Position sizing</div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-muted sm:grid-cols-3">
        <span>Quantity <b className="num text-text">{fmtNum(sizing.qty, 6)}</b></span>
        <span>Notional <b className="num text-text">{fmtUsd(sizing.notional)}</b></span>
        <span>Risk amount <b className="num text-text">{fmtUsd(sizing.risk_amount)}</b></span>
        <span>Entry estimate <b className="num text-text">{fmtPrice(sizing.entry_estimate)}</b></span>
        <span>Loss per unit <b className="num text-text">{fmtPrice(sizing.per_unit_loss)}</b></span>
        <span>Binding limit <b className="text-text">{titleCase(sizing.binding)}</b></span>
      </div>
      {Object.keys(sizing.candidates).length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-faint">
          {Object.entries(sizing.candidates).map(([k, v]) => (
            <span key={k}>
              {titleCase(k)}: <span className="num">{v === null ? "unlimited" : fmtNum(v, 6)}</span>
            </span>
          ))}
        </div>
      )}
      {sizing.rejected && <div className="mt-1 text-down">{sizing.rejected}</div>}
    </div>
  );
}
