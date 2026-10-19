import { useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLiveEvents } from "./live";

// Refetch the given queries when the engine reports activity over the WebSocket, at most once
// per `minIntervalMs` (a fast replay publishes many events per second).
export function useLiveRefresh(type: string, keys: string[][], minIntervalMs = 1500): void {
  const qc = useQueryClient();
  const last = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  useLiveEvents((e) => {
    if (e.type !== type) return;
    const run = () => {
      last.current = Date.now();
      timer.current = undefined;
      for (const key of keys) qc.invalidateQueries({ queryKey: key });
    };
    const wait = last.current + minIntervalMs - Date.now();
    if (wait <= 0) run();
    else if (timer.current === undefined) timer.current = window.setTimeout(run, wait);
  });
}
