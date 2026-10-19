// Shapes of the backend's JSON responses. Every money value is simulated paper money.

export type Freshness = {
  exchange: string;
  pair: string;
  timeframe: string;
  status: "fresh" | "stale" | "empty";
  stale: boolean;
  last_open_ts: number | null;
  last_close_ts: number | null;
  age_s: number | null;
  behind_bars: number | null;
  checked_at: number;
  source: string | null;
};

export type Candle = { time: number; ts: number; open: number; high: number; low: number; close: number; volume: number; source?: string | null };
export type Point = { time: number; value: number | null };

export type ChartPayload = {
  exchange: string;
  pair: string;
  timeframe: string;
  candles: Candle[];
  forming: Candle | null;
  indicators: Record<"ema20" | "ema50" | "ema200" | "rsi14" | "atr14" | "bb_upper" | "bb_mid" | "bb_lower", Point[]>;
  sources: Record<string, number>;
  derived_from: string | null;
  freshness: Freshness;
  notes: string[];
  data_label: string;
};

export type Ticker = {
  pair: string;
  last: number;
  bid: number | null;
  ask: number | null;
  spread_pct: number | null;
  open_24h: number | null;
  high_24h: number | null;
  low_24h: number | null;
  change_24h: number | null;
  change_pct_24h: number | null;
  base_volume_24h: number | null;
  quote_volume_24h: number | null;
  ts: number;
  received_ms: number;
  source: string;
  age_s?: number;
  stale?: boolean;
};

export type OrderBook = {
  pair: string;
  bids: [number, number][];
  asks: [number, number][];
  best_bid: number | null;
  best_ask: number | null;
  spread_pct: number | null;
  ts: number;
  source: string;
};

export type ClientStatus = {
  reachable: boolean | null;
  last_success_ms: number | null;
  last_error: string | null;
  last_error_ms: number | null;
  consecutive_failures: number;
  cooldown_s: number;
  latency_ms: number | null;
};

export type MarketStatus = {
  status: "ok" | "degraded" | "error";
  exchange: string;
  rest: ClientStatus;
  websocket: { enabled: boolean; connected: boolean; since_ms: number | null; last_message_ms: number | null; last_message_age_s: number | null; last_error: string | null; reconnects: number; streams: number; messages: number };
  series: Freshness[];
  tickers: Record<string, number | null>;
};

export type PairsInfo = {
  exchange: string;
  pairs: string[];
  trading_pairs: string[];
  timeframes: string[];
  signal_timeframe: string;
  confirm_timeframes: string[];
  local_series: { exchange: string; pair: string; timeframe: string; candles: number; first_ts: number; last_ts: number }[];
};

export type InventoryRow = {
  exchange: string;
  pair: string;
  timeframe: string;
  candles: number;
  first_ts: number;
  last_ts: number;
  expected: number;
  missing: number;
  gap_count: number;
  coverage_pct: number;
  sources: Record<string, number>;
  last_sync_at: number | null;
  last_sync_status: string | null;
  last_error: string | null;
};

export type Job = {
  id: string;
  kind: string;
  label: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  message: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  result: unknown;
  error: string | null;
};

export type HealthComponent = { status: "ok" | "degraded" | "error" | "disabled"; [k: string]: unknown };
export type Health = {
  status: "ok" | "degraded" | "error";
  ts: number;
  uptime_s: number;
  critical_error: { source: string; message: string; ts: number; context: Record<string, unknown> } | null;
  components: Record<string, HealthComponent>;
};

export type SettingsPayload = {
  effective: Record<string, Record<string, unknown>>;
  file: Record<string, Record<string, unknown>>;
  overrides: Record<string, Record<string, unknown>>;
  editable: Record<string, string[]>;
  hard_limits: Record<string, number | string>;
  config_hash: string;
  secrets: { anthropic_api_key_present: boolean };
};
