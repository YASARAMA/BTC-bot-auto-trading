import { useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "./Button";
import { Field, Input } from "./Form";

export function Dialog({ open, onClose, title, children, footer, width = 480 }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    panel.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" onClick={onClose} />
      <div
        ref={panel}
        tabIndex={-1}
        style={{ maxWidth: width }}
        className="relative w-full animate-fade-in rounded-xl border border-border-strong bg-surface shadow-2xl outline-none"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h3 className="text-sm font-semibold">{title}</h3>
          <button type="button" onClick={onClose} className="rounded p-1 text-muted hover:bg-surface-2 hover:text-text" aria-label="Close">
            <X className="size-4" />
          </button>
        </div>
        <div className="max-h-[70vh] overflow-auto px-4 py-4 text-[13px] leading-6 text-text/90">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-border px-4 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({ open, onClose, onConfirm, title, children, confirmLabel = "Confirm", danger, requireNote, busy, confirmDisabled }: {
  open: boolean;
  onClose: () => void;
  onConfirm: (note: string) => void;
  title: ReactNode;
  children: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  requireNote?: string;
  busy?: boolean;
  confirmDisabled?: boolean;
}) {
  const [note, setNote] = useState("");
  useEffect(() => {
    if (open) setNote("");
  }, [open]);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            variant={danger ? "danger" : "primary"}
            loading={busy}
            disabled={confirmDisabled || (!!requireNote && note.trim().length < 3)}
            onClick={() => onConfirm(note.trim())}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
      {requireNote && (
        <Field label={requireNote} className="mt-3">
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Recorded with the action" maxLength={500} />
        </Field>
      )}
    </Dialog>
  );
}
