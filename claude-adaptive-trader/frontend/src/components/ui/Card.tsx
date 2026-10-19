import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export function Card({ title, subtitle, actions, children, className, bodyClassName, icon, id }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
  icon?: ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className={cn("rounded-xl border border-border bg-surface/80 shadow-[0_1px_0_0_rgba(255,255,255,0.02)_inset] animate-fade-in min-w-0", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-border/70 px-4 py-3">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-[13px] font-semibold tracking-wide text-text">
              {icon && <span className="text-muted">{icon}</span>}
              <span className="truncate">{title}</span>
            </h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}
