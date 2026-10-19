import { cloneElement, isValidElement, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export function Field({ label, hint, error, children, className, group }: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  className?: string;
  group?: boolean;
}) {
  const id = useId();
  // The control is named by the label text only (not by its own options or value).
  const control = !group && isValidElement<{ "aria-labelledby"?: string }>(children) ? cloneElement(children, { "aria-labelledby": id }) : children;
  const body = (
    <>
      <span id={id} className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {control}
      {error ? <span className="mt-1 block text-xs text-down">{error}</span> : hint ? <span className="mt-1 block text-[11px] leading-4 text-faint">{hint}</span> : null}
    </>
  );
  // A <label> names only its first control, so a set of buttons is a labelled group instead.
  if (group) {
    return (
      <div role="group" aria-labelledby={id} className={cn("block min-w-0", className)}>
        {body}
      </div>
    );
  }
  return <label className={cn("block min-w-0", className)}>{body}</label>;
}

const control = "h-9 w-full rounded-lg border border-border-strong bg-surface-2 px-2.5 text-[13px] text-text placeholder:text-faint focus:border-accent focus:outline-none disabled:opacity-50";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(control, "num", props.className)} />;
}

export function Select({ options, className, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & {
  options: { value: string; label: string; disabled?: boolean }[];
}) {
  return (
    <select {...rest} className={cn(control, "cursor-pointer pr-7", className)}>
      {options.map((o) => (
        <option key={o.value} value={o.value} disabled={o.disabled}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Switch({ checked, onChange, label, disabled }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="inline-flex items-center gap-2 text-[13px] text-text disabled:opacity-50"
    >
      <span className={cn("relative h-5 w-9 rounded-full border transition-colors", checked ? "border-accent bg-accent-strong" : "border-border-strong bg-surface-3")}>
        <span className={cn("absolute top-0.5 size-3.5 rounded-full bg-white transition-all", checked ? "left-[18px]" : "left-0.5")} />
      </span>
      {label}
    </button>
  );
}

export function Segmented<T extends string>({ value, options, onChange, size = "md", className }: {
  value: T;
  options: { value: T; label: ReactNode; title?: string }[];
  onChange: (v: T) => void;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <div role="tablist" className={cn("inline-flex rounded-lg border border-border bg-surface-2 p-0.5", className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          title={o.title}
          aria-selected={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-md font-medium transition-colors",
            size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs",
            o.value === value ? "bg-surface-3 text-text shadow-sm" : "text-muted hover:text-text",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ value, tabs, onChange }: {
  value: T;
  tabs: { value: T; label: ReactNode }[];
  onChange: (v: T) => void;
}) {
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-border">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          type="button"
          aria-selected={t.value === value}
          onClick={() => onChange(t.value)}
          className={cn(
            "-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-[13px] font-medium transition-colors",
            t.value === value ? "border-accent text-text" : "border-transparent text-muted hover:text-text",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Meter({ value, max, tone = "accent", label, detail }: {
  value: number | null | undefined;
  max: number;
  tone?: "accent" | "up" | "down" | "warn";
  label?: ReactNode;
  detail?: ReactNode;
}) {
  const pct = value === null || value === undefined || !Number.isFinite(value) || max <= 0 ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
  const bar = pct >= 90 ? "bg-down" : pct >= 70 ? "bg-warn" : { accent: "bg-accent", up: "bg-up", down: "bg-down", warn: "bg-warn" }[tone];
  return (
    <div className="min-w-0">
      {(label || detail) && (
        <div className="mb-1 flex items-center justify-between gap-2 text-xs">
          <span className="truncate text-muted">{label}</span>
          <span className="num shrink-0 text-text">{detail}</span>
        </div>
      )}
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
        <div className={cn("h-full rounded-full transition-all", bar)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
