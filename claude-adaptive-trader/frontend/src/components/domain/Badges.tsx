import { Badge, type Tone } from "../ui/Badge";
import { titleCase } from "../../lib/format";

const REGIME: Record<string, { tone: Tone; label: string }> = {
  trend_up: { tone: "up", label: "Trend up" },
  trend_down: { tone: "down", label: "Trend down" },
  range: { tone: "info", label: "Range" },
  high_volatility_breakout: { tone: "warn", label: "Volatility breakout" },
  uncertain: { tone: "neutral", label: "Uncertain" },
};

export const REGIME_COLORS: Record<string, string> = {
  trend_up: "#2ebd85",
  trend_down: "#f6465d",
  range: "#4ea8ff",
  high_volatility_breakout: "#f0b90b",
  uncertain: "#5d687a",
};

export function regimeLabel(r: string | null | undefined): string {
  if (!r) return "—";
  return REGIME[r]?.label ?? titleCase(r);
}

export function RegimeBadge({ regime, confidence }: { regime: string | null | undefined; confidence?: number | null }) {
  if (!regime) return <Badge>unknown</Badge>;
  const r = REGIME[regime] ?? { tone: "neutral" as Tone, label: titleCase(regime) };
  return (
    <Badge tone={r.tone} dot title={confidence !== undefined && confidence !== null ? `confidence ${(confidence * 100).toFixed(0)}%` : undefined}>
      {r.label}
      {confidence !== undefined && confidence !== null && <span className="text-[10px] opacity-75">{(confidence * 100).toFixed(0)}%</span>}
    </Badge>
  );
}

export function EngineBadge({ status }: { status: string | null | undefined }) {
  const tone: Tone = status === "running" ? "up" : status === "paused" ? "warn" : status === "error" ? "down" : "neutral";
  return (
    <Badge tone={tone} dot>
      {status ?? "unknown"}
    </Badge>
  );
}

export function VerdictBadge({ verdict }: { verdict: string | null | undefined }) {
  if (!verdict) return <Badge>no verdict</Badge>;
  if (verdict === "passed_preliminary_gates") return <Badge tone="up" title="Passed the preliminary gates; not proof of future profitability">passed gates</Badge>;
  if (verdict === "no_reliable_edge") return <Badge tone="down" title="The evidence does not show a reliable edge">no reliable edge</Badge>;
  return <Badge>{titleCase(verdict)}</Badge>;
}

export function VersionStatusBadge({ status }: { status: string }) {
  const tone: Tone = { approved: "up", validated: "info", research: "neutral", rejected: "down", retired: "neutral" }[status] as Tone ?? "neutral";
  return <Badge tone={tone}>{status}</Badge>;
}

export function ExperimentStatusBadge({ status }: { status: string }) {
  const tone: Tone = status === "completed" ? "up" : status === "failed" ? "down" : "warn";
  return <Badge tone={tone} dot>{status}</Badge>;
}

export function SideBadge({ side }: { side: string }) {
  return <Badge tone={side === "buy" ? "up" : "down"}>{side.toUpperCase()}</Badge>;
}

export function OrderStatusBadge({ status }: { status: string }) {
  const tone: Tone = status === "filled" ? "up" : status === "rejected" ? "down" : status === "cancelled" ? "neutral" : "warn";
  return <Badge tone={tone}>{status.replace("_", " ")}</Badge>;
}

export const KIND_LABEL: Record<string, string> = {
  backtest: "Backtest",
  validation: "Validation protocol",
  selector_walk_forward: "Selector walk-forward",
  final_test: "Final test",
};
