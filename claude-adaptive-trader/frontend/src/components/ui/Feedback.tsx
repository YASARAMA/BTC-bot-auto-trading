import type { ReactNode } from "react";
import { CircleAlert, Inbox, LoaderCircle, RefreshCw } from "lucide-react";
import { cn } from "../../lib/cn";
import { errorMessage } from "../../lib/api";
import { Button } from "./Button";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton rounded-md", className)} />;
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-6 w-full" />
      ))}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle className={cn("size-4 animate-spin text-muted", className)} />;
}

export function EmptyState({ title, children, icon, action, className }: {
  title: string;
  children?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-10 text-center", className)}>
      <div className="text-faint">{icon ?? <Inbox className="size-6" />}</div>
      <div className="text-sm font-medium text-text">{title}</div>
      {children && <div className="max-w-md text-xs leading-5 text-muted">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, title = "Could not load this data", className }: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 rounded-lg border border-down/30 bg-down/5 px-6 py-8 text-center", className)}>
      <CircleAlert className="size-5 text-down" />
      <div className="text-sm font-medium text-text">{title}</div>
      <div className="max-w-lg text-xs leading-5 text-muted">{errorMessage(error)}</div>
      {onRetry && (
        <Button size="sm" variant="secondary" icon={<RefreshCw className="size-3.5" />} onClick={onRetry} className="mt-1">
          Retry
        </Button>
      )}
    </div>
  );
}

export function Notice({ tone = "info", children, icon, className }: {
  tone?: "info" | "warn" | "down" | "up";
  children: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  const styles = {
    info: "border-info/30 bg-info/5 text-info",
    warn: "border-warn/30 bg-warn/5 text-warn",
    down: "border-down/30 bg-down/5 text-down",
    up: "border-up/30 bg-up/5 text-up",
  }[tone];
  return (
    <div className={cn("flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-5", styles, className)}>
      {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
      <div className="min-w-0 text-text/90">{children}</div>
    </div>
  );
}
