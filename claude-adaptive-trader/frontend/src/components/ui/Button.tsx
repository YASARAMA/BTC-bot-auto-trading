import type { ButtonHTMLAttributes, ReactNode } from "react";
import { LoaderCircle } from "lucide-react";
import { cn } from "../../lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";

const styles: Record<Variant, string> = {
  primary: "bg-accent-strong text-white hover:bg-accent border-transparent",
  secondary: "bg-surface-2 text-text hover:bg-surface-3 border-border-strong",
  ghost: "bg-transparent text-muted hover:text-text hover:bg-surface-2 border-transparent",
  danger: "bg-down/15 text-down hover:bg-down/25 border-down/40",
  success: "bg-up/15 text-up hover:bg-up/25 border-up/40",
};

export function Button({ variant = "secondary", size = "md", loading, icon, children, className, disabled, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
}) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-[13px]",
        styles[variant],
        className,
      )}
    >
      {loading ? <LoaderCircle className="size-3.5 animate-spin" /> : icon}
      {children}
    </button>
  );
}
