// Shared look of the Recharts charts so they match the rest of the dark UI.
import type { CSSProperties } from "react";

export const CC = {
  up: "#2ebd85",
  down: "#f6465d",
  accent: "#7c8cff",
  info: "#4ea8ff",
  warn: "#f0b90b",
  muted: "#8f99ab",
  faint: "#5d687a",
  grid: "rgba(42,52,70,0.45)",
  benchmark: "#8f99ab",
};

export const axisProps = {
  stroke: CC.faint,
  tick: { fill: CC.muted, fontSize: 11 },
  tickLine: false,
  axisLine: { stroke: "rgba(42,52,70,0.8)" },
} as const;

export const tooltipStyle: { contentStyle: CSSProperties; labelStyle: CSSProperties; itemStyle: CSSProperties; cursor: { stroke: string } } = {
  contentStyle: { background: "#121822", border: "1px solid #2a3446", borderRadius: 8, fontSize: 12, padding: "6px 10px" },
  labelStyle: { color: "#e6ebf2", marginBottom: 2 },
  itemStyle: { padding: 0 },
  cursor: { stroke: "rgba(124,140,255,0.35)" },
};

export function shortDate(ms: number): string {
  return new Date(ms).toLocaleDateString("en-GB", { month: "short", day: "2-digit", timeZone: "UTC" });
}
