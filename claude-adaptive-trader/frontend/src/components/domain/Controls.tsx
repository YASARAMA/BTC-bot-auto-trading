import { useState } from "react";
import { OctagonX, ShieldCheck } from "lucide-react";
import { post } from "../../lib/api";
import { fmtDateTime } from "../../lib/format";
import { useAction } from "../../lib/useAction";
import type { KillSwitchState } from "../../lib/types";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/Dialog";

export const ENGINE_KEYS = [["paper-status"], ["paper-status-lite"], ["dashboard"], ["risk-status"], ["positions"], ["orders"], ["decisions"], ["trades"], ["equity"], ["paper-sessions"]];

export function KillSwitchButton({ state, size = "md" }: { state: KillSwitchState | undefined; size?: "sm" | "md" }) {
  const [open, setOpen] = useState(false);
  const on = !!state?.on;
  const action = useAction((reason: string) => post("/api/paper/kill-switch", { on: !on, reason }), {
    success: on ? "Kill switch turned off" : "Kill switch ON: no new entries",
    invalidate: ENGINE_KEYS,
    onSuccess: () => setOpen(false),
  });
  return (
    <>
      <Button
        size={size}
        variant={on ? "success" : "danger"}
        icon={on ? <ShieldCheck className="size-4" /> : <OctagonX className="size-4" />}
        onClick={() => setOpen(true)}
        title={on ? `On since ${fmtDateTime(state?.at)} (${state?.reason ?? ""})` : "Stop all new entries immediately"}
      >
        {on ? "Turn kill switch off" : "Kill switch"}
      </Button>
      <ConfirmDialog
        open={open}
        onClose={() => setOpen(false)}
        title={on ? "Turn the kill switch off?" : "Turn the kill switch on?"}
        confirmLabel={on ? "Turn off" : "Turn on"}
        danger={!on}
        busy={action.isPending}
        requireNote="Reason (recorded in the log)"
        onConfirm={(note) => action.mutate(note)}
      >
        {on ? (
          <p>New entries will be allowed again, subject to all fourteen risk checks and the circuit breakers.</p>
        ) : (
          <p>
            Every new entry is blocked immediately and pending entry orders are cancelled. Open positions keep their stops and exits.
            The switch stays on across restarts until a person turns it off.
          </p>
        )}
      </ConfirmDialog>
    </>
  );
}
