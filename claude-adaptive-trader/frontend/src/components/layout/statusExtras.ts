import { useQuery } from "@tanstack/react-query";
import type { Tone } from "../ui/Badge";
import { get } from "../../lib/api";

type PaperStatusLite = { status: "running" | "paused" | "stopped" | "error" | "idle"; kill_switch: boolean };

// Bot state and kill switch for the top bar. While the backend cannot be reached it shows
// nothing rather than a guessed state.
export function useStatusExtras(): { botStatus: string | null; botTone: Tone; killSwitch: boolean } {
  const q = useQuery({
    queryKey: ["paper-status-lite"],
    queryFn: ({ signal }) => get<PaperStatusLite>("/api/paper/status-lite", signal),
    refetchInterval: 5_000,
    retry: 0,
  });
  if (!q.data) return { botStatus: null, botTone: "neutral", killSwitch: false };
  const s = q.data.status;
  const tone: Tone = s === "running" ? "up" : s === "paused" ? "warn" : s === "error" ? "down" : "neutral";
  return { botStatus: s, botTone: tone, killSwitch: q.data.kill_switch };
}
