// One WebSocket to the backend for the whole app: live tickers, candle updates for the charts
// that are open, and engine events. Reconnects with backoff; components subscribe to it.
import { useEffect, useRef, useSyncExternalStore } from "react";

export type LiveEvent = { type: string; [key: string]: unknown };
type LiveState = "connecting" | "open" | "closed";

class LiveConnection {
  private ws: WebSocket | null = null;
  private listeners = new Set<(e: LiveEvent) => void>();
  private stateListeners = new Set<() => void>();
  private subs = new Map<string, number>();
  private backoff = 1000;
  private timer: number | undefined;
  private started = false;
  state: LiveState = "closed";
  lastMessageAt = 0;

  start(): void {
    if (this.started) return;
    this.started = true;
    this.connect();
  }

  private setState(s: LiveState): void {
    this.state = s;
    this.stateListeners.forEach((l) => l());
  }

  private connect(): void {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    this.setState("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(`${proto}://${window.location.host}/ws/stream`);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.backoff = 1000;
      this.setState("open");
      for (const key of this.subs.keys()) this.sendSub("subscribe", key);
    };
    ws.onmessage = (msg) => {
      this.lastMessageAt = Date.now();
      let event: LiveEvent;
      try {
        event = JSON.parse(String(msg.data)) as LiveEvent;
      } catch {
        return;
      }
      this.listeners.forEach((l) => l(event));
    };
    ws.onclose = () => {
      this.ws = null;
      this.setState("closed");
      this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  private scheduleReconnect(): void {
    window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => this.connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 30000);
  }

  private sendSub(op: "subscribe" | "unsubscribe", key: string): void {
    const [pair, timeframe] = key.split("|");
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ op, pair, timeframe }));
  }

  subscribe(pair: string, timeframe: string): () => void {
    const key = `${pair}|${timeframe}`;
    const n = this.subs.get(key) ?? 0;
    this.subs.set(key, n + 1);
    if (n === 0) this.sendSub("subscribe", key);
    return () => {
      const m = (this.subs.get(key) ?? 1) - 1;
      if (m <= 0) {
        this.subs.delete(key);
        this.sendSub("unsubscribe", key);
      } else this.subs.set(key, m);
    };
  }

  on(listener: (e: LiveEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onState(listener: () => void): () => void {
    this.stateListeners.add(listener);
    return () => this.stateListeners.delete(listener);
  }
}

export const live = new LiveConnection();

export function useLiveState(): LiveState {
  return useSyncExternalStore((cb) => live.onState(cb), () => live.state);
}

export function useLiveEvents(handler: (e: LiveEvent) => void): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => live.on((e) => ref.current(e)), []);
}

export function useKlineSubscription(pair: string | null, timeframe: string | null): void {
  useEffect(() => {
    if (!pair || !timeframe) return;
    return live.subscribe(pair, timeframe);
  }, [pair, timeframe]);
}
