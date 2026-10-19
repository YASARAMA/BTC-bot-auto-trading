import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export type Tone = "neutral" | "up" | "down" | "warn" | "info" | "accent";

const tones: Record<Tone, string> = {
  neutral: "bg-surface-3 text-muted border-border-strong",
  up: "bg-up/10 text-up border-up/30",
  down: "bg-down/10 text-down border-down/30",
  warn: "bg-warn/10 text-warn border-warn/30",
  info: "bg-info/10 text-info border-info/30",
  accent: "bg-accent/10 text-accent border-accent/30",
};

export function Badge({ tone = "neutral", children, className, dot, title }: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  dot?: boolean;
  title?: string;
}) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4 whitespace-nowrap", tones[tone], className)}>
      {dot && <span className={cn("size-1.5 rounded-full", dotColor(tone))} />}
      {children}
    </span>
  );
}

function dotColor(tone: Tone): string {
  return { neutral: "bg-faint", up: "bg-up", down: "bg-down", warn: "bg-warn", info: "bg-info", accent: "bg-accent" }[tone];
}

export function SimulatedTag({ className }: { className?: string }) {
  return (
    <Badge tone="accent" className={className} title="Simulated paper-trading value, not real money">
      SIMULATED
    </Badge>
  );
}
