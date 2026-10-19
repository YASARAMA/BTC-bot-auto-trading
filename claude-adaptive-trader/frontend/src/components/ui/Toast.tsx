import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { cn } from "../../lib/cn";

type ToastKind = "success" | "error" | "info";
type Toast = { id: number; kind: ToastKind; title: string; detail?: string };

const ToastContext = createContext<(kind: ToastKind, title: string, detail?: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: ToastKind, title: string, detail?: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-4), { id, kind, title, detail }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 9000 : 4500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={cn(
              "pointer-events-auto flex animate-fade-in items-start gap-2.5 rounded-lg border bg-surface px-3 py-2.5 shadow-xl",
              t.kind === "success" && "border-up/40",
              t.kind === "error" && "border-down/40",
              t.kind === "info" && "border-border-strong",
            )}
          >
            {t.kind === "success" ? <CircleCheck className="mt-0.5 size-4 text-up" /> : t.kind === "error" ? <CircleAlert className="mt-0.5 size-4 text-down" /> : <Info className="mt-0.5 size-4 text-info" />}
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-medium">{t.title}</div>
              {t.detail && <div className="mt-0.5 break-words text-xs text-muted">{t.detail}</div>}
            </div>
            <button type="button" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))} className="text-faint hover:text-text" aria-label="Dismiss">
              <X className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
