import type { ReactNode } from "react";
import { Info } from "lucide-react";
import { cn } from "../../lib/cn";
import { Skeleton } from "./Feedback";

export function Kpi({ label, value, sub, tone, hint, loading, badge, className }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "up" | "down" | "flat" | "warn";
  hint?: string;
  loading?: boolean;
  badge?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("relative min-w-0 rounded-xl border border-border bg-surface/80 px-4 py-3.5", className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 truncate text-[11px] font-medium uppercase tracking-wider text-muted">
          {label}
          {hint && (
            <span title={hint} className="cursor-help text-faint hover:text-muted">
              <Info className="size-3" />
            </span>
          )}
        </span>
        {badge}
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-28" />
      ) : (
        <div className={cn("num mt-1.5 truncate text-[22px] font-semibold leading-7 tracking-tight",
          tone === "up" && "text-up", tone === "down" && "text-down", tone === "warn" && "text-warn")}>
          {value}
        </div>
      )}
      {sub && !loading && <div className="num mt-1 truncate text-xs text-muted">{sub}</div>}
    </div>
  );
}
