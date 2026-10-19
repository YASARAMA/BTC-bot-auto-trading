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

// ----- paper trading, portfolio, risk ---------------------------------------------------------------

export type EngineStatus = "running" | "paused" | "stopped" | "error" | "idle";

export type PaperSession = {
  id: string;
  created_at: number;
  data_mode: "live" | "replay";
  strategy_mode: "adaptive" | "forward_test";
  forward_strategy: string | null;
  pairs: string[];
  timeframe: string;
  exchange: string;
  initial_cash: number;
  clock_ts: number | null;
  replay_start_ts: number | null;
  replay_end_ts: number | null;
  replay_speed: number | null;
  confirm_timeframes: string[];
  started_at: number | null;
};

export type PortfolioSnapshot = {
  cash: number;
  equity: number;
  exposure: number;
  exposure_pct: number;
  open_risk: number;
  realized_pnl: number;
  unrealized_pnl: number;
  fees_paid: number;
  slippage_paid: number;
  positions: number;
};

export type LivePosition = {
  pair: string;
  qty: number;
  entry_price: number;
  entry_ts: number;
  entry_fee: number;
  entry_slippage: number;
  stop: number;
  target: number | null;
  invalidation: number | null;
  initial_risk: number;
  strategy: string;
  strategy_version: string;
  regime: string;
  environment: string | null;
  entry_reason: string;
  signal_ts: number;
  time_stop_bars: number | null;
  key: string;
  bars_held: number;
  exit_qty: number;
  pending_exit: boolean;
};

export type PendingOrder = {
  client_order_id: string;
  pair: string;
  side: "buy" | "sell";
  purpose: string;
  reason: string;
  created_ts: number;
  qty: number;
  ref_price: number;
  strategy: string;
  regime: string;
  stop: number | null;
  target: number | null;
  status: string;
};

export type Breakers = {
  day_start_ts: number;
  day_start_equity: number;
  peak_equity: number;
  daily_tripped: boolean;
  daily_tripped_at: number | null;
  drawdown_tripped: boolean;
  drawdown_tripped_at: number | null;
  daily_loss_pct: number;
  drawdown_pct: number;
};

export type KillSwitchState = { on: boolean; by: string | null; at: number | null; reason: string | null };

export type PairEngineStatus = {
  last_candle_ts?: number;
  regime?: string;
  regime_confidence?: number;
  regime_reason?: string;
  close?: number;
  processed_at?: number;
  waiting?: string | null;
};

export type PaperStatus = {
  status: EngineStatus;
  session: PaperSession | null;
  label?: string;
  portfolio?: PortfolioSnapshot;
  positions?: LivePosition[];
  pending_orders?: PendingOrder[];
  breakers?: Breakers;
  kill_switch: KillSwitchState;
  engine?: { healthy: boolean; problems: string[] };
  pairs?: Record<string, PairEngineStatus>;
  stats?: { decisions: Record<string, number>; rejections: Record<string, number> };
  last_error: string | null;
  thread_alive?: boolean;
};

export type SessionRow = {
  id: string;
  created_at: number;
  updated_at: number;
  status: EngineStatus;
  data_mode: "live" | "replay";
  strategy_mode: "adaptive" | "forward_test";
  forward_strategy: string | null;
  pairs: string[];
  timeframe: string;
  exchange: string;
  initial_cash: number;
  cash: number;
  started_at: number | null;
  stopped_at: number | null;
  last_error: string | null;
  note: string | null;
};

export type CheckResult = { name: string; passed: boolean; detail: string };

export type Sizing = {
  qty: number;
  notional: number;
  risk_amount: number;
  per_unit_loss: number;
  entry_estimate: number;
  binding: string;
  candidates: Record<string, number | null>;
  rejected: string | null;
};

export type Decision = {
  id: number;
  session_id: string;
  ts: number;
  pair: string;
  timeframe: string;
  regime: string;
  regime_confidence: number | null;
  strategy: string | null;
  strategy_version: string | null;
  action: "enter" | "exit" | "no_trade" | "hold";
  accepted: boolean;
  reason: string;
  checks: CheckResult[] | null;
  signal: {
    entry_ref?: number;
    stop?: number;
    target?: number | null;
    invalidation?: number | null;
    confidence?: number;
    qty?: number;
    sizing?: Sizing | null;
    notes?: string[];
    environment?: string | null;
    scores?: Record<string, unknown> | null;
  } | null;
};

export type DbPosition = {
  id: number;
  session_id: string;
  pair: string;
  status: "open" | "closed";
  qty: number;
  qty_exited: number;
  entry_price: number;
  entry_ts: number;
  entry_fee: number;
  stop_price: number;
  take_profit: number | null;
  initial_risk: number;
  strategy: string;
  strategy_version: string;
  regime: string;
  environment: string | null;
  entry_reason: string | null;
  bars_held: number;
  closed_ts: number | null;
  exit_reason: string | null;
  mark?: number | null;
  unrealized_pnl?: number;
  unrealized_pct?: number;
};

export type OrderRow = {
  id: number;
  session_id: string;
  client_order_id: string;
  pair: string;
  side: "buy" | "sell";
  purpose: string;
  order_type: string;
  status: "pending" | "filled" | "partially_filled" | "cancelled" | "rejected";
  requested_qty: number;
  filled_qty: number;
  ref_price: number | null;
  fill_price: number | null;
  fee: number;
  slippage_cost: number;
  strategy: string | null;
  regime: string | null;
  created_ts: number;
  filled_ts: number | null;
  reason: string | null;
  reject_reason: string | null;
  stop_price: number | null;
  take_profit: number | null;
  exit_reason: string | null;
};

export type TradeRow = {
  id: number;
  source: "paper" | "backtest";
  run_id: string;
  scenario: string;
  segment: string | null;
  position_key: string;
  pair: string;
  timeframe: string;
  strategy: string;
  strategy_version: string;
  regime: string;
  environment: string | null;
  entry_ts: number;
  exit_ts: number;
  entry_price: number;
  exit_price: number;
  qty: number;
  stop_price: number | null;
  take_profit: number | null;
  gross_pnl: number;
  fees: number;
  slippage_cost: number;
  net_pnl: number;
  return_pct: number;
  initial_risk: number | null;
  r_multiple: number | null;
  exit_reason: string;
  entry_reason: string | null;
  mae_pct: number | null;
  mfe_pct: number | null;
  bars_held: number | null;
};

export type EquityPoint = {
  ts: number;
  equity: number;
  cash?: number;
  exposure?: number;
  open_risk?: number;
  drawdown_pct?: number;
  benchmark?: number | null;
};

export type GroupStats = Record<string, { trades: number; net_pnl?: number; win_rate_pct?: number | null; expectancy?: number | null; expectancy_r?: number | null; expectancy_pct?: number | null; profit_factor?: number | null; fees?: number | null }>;

export type Metrics = {
  trades: number;
  initial_cash: number;
  final_equity: number;
  net_profit: number;
  net_return_pct: number;
  gross_return_pct: number;
  total_fees: number;
  slippage_estimate: number;
  fee_share_of_gross_pct: number | null;
  win_rate_pct: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  avg_win_r: number | null;
  avg_loss_r: number | null;
  expectancy: number | null;
  expectancy_r: number | null;
  expectancy_pct: number | null;
  profit_factor: number | null;
  payoff_ratio: number | null;
  longest_losing_streak: number;
  longest_winning_streak: number;
  best_trade: number | null;
  worst_trade: number | null;
  net_without_best_trade: number | null;
  best_trade_share_pct: number | null;
  avg_bars_held: number | null;
  avg_mae_pct: number | null;
  avg_mfe_pct: number | null;
  turnover: number | null;
  max_drawdown_pct: number | null;
  longest_drawdown_bars: number;
  exposure_pct: number | null;
  sharpe: number | null;
  sortino: number | null;
  cagr_pct: number | null;
  calmar: number | null;
  days: number;
  best_month_share_pct: number | null;
  benchmark_return_pct: number | null;
  excess_return_pct: number | null;
  benchmark_max_drawdown_pct: number | null;
  monthly_returns_pct?: Record<string, number | null>;
  by_regime?: GroupStats;
  by_pair?: GroupStats;
  by_strategy?: GroupStats;
  by_exit_reason?: GroupStats;
  by_environment?: GroupStats;
  notes: string[];
};

export type RiskStatus = {
  limits: Record<string, unknown>;
  hard_limits: Record<string, number>;
  checks: string[];
  utilization: {
    equity: number;
    open_risk: number;
    open_risk_pct: number;
    open_risk_limit_pct: number;
    exposure_pct: number;
    exposure_limit_pct: number;
    positions: number;
    positions_limit: number;
    daily_loss_pct: number;
    daily_loss_limit_pct: number;
    drawdown_pct: number;
    drawdown_limit_pct: number;
  };
  breakers: Partial<Breakers>;
  kill_switch: KillSwitchState;
  engine?: { healthy: boolean; problems: string[] };
  session: PaperSession | null;
  stop_note: string;
};

// ----- regimes, strategies, selector ---------------------------------------------------------------------

export type RegimeName = "trend_up" | "trend_down" | "range" | "high_volatility_breakout" | "uncertain";

export type PairRegime = {
  available: boolean;
  reason: string;
  candle_ts?: number;
  stale?: boolean;
  regime?: RegimeName;
  confidence?: number;
  direction?: number;
  explain?: { features: Record<string, number | null>; thresholds: Record<string, number> };
  history?: { ts: number; regime: RegimeName }[];
};

export type RegimesPayload = { timeframe: string; pairs: Record<string, PairRegime> };

export type RegimeEvidence = { trades: number; expectancy_r: number | null; profit_factor: number | null; win_rate_pct: number | null; lower_bound_r: number | null };

export type Evidence = {
  strategy: string;
  version: string;
  status: string;
  complexity: number;
  required_regimes: string[];
  by_regime: Record<string, RegimeEvidence>;
  max_drawdown_pct: number | null;
  stress_expectancy_r: number | null;
  recent_trades: number;
  recent_expectancy_r: number | null;
  source: string;
};

export type Gate = { gate: string; passed: boolean; value: unknown; threshold: unknown; detail: string };
export type GateResult = { verdict: "passed_preliminary_gates" | "no_reliable_edge"; passed: boolean; failed: string[]; gates: Gate[]; message: string };

export type StrategyVersion = {
  id: number;
  strategy: string;
  version: string;
  params: Record<string, unknown>;
  params_hash: string;
  status: "research" | "validated" | "approved" | "rejected" | "retired";
  created_at: number;
  created_by: string;
  parent_id: number | null;
  hypothesis: string | null;
  notes: string | null;
  validation: GateResult | null;
  validation_experiment_id: string | null;
  approved_at: number | null;
  approved_by: string | null;
  approval_note: string | null;
};

export type JsonSchemaProp = { type?: string; default?: unknown; description?: string; minimum?: number; maximum?: number; exclusiveMinimum?: number; exclusiveMaximum?: number; title?: string };

export type StrategySpec = {
  name: string;
  title: string;
  code_version: string;
  version: string;
  hypothesis: string;
  direction: string;
  required_regimes: string[];
  entry_rules: string[];
  exit_rules: string[];
  stop_rule: string;
  target_rule: string;
  params: Record<string, unknown>;
  tunable: string[];
  complexity: number;
  params_schema: { properties?: Record<string, JsonSchemaProp> };
  required_data: string;
};

export type StrategyInfo = {
  name: string;
  title: string;
  spec: StrategySpec;
  versions: StrategyVersion[];
  approved: StrategyVersion | null;
  evidence: Evidence | null;
  evidence_version: string | null;
};

export type SelectorRow = { strategy: string; version: string; eligible: boolean; score: number | null; reasons: string[] };

export type SelectorPayload = {
  approved: Record<string, string>;
  evidence: Record<string, Evidence>;
  pairs: Record<string, { regime: string; table: SelectorRow[]; state: { active_strategy: string | null; challenger: string | null; challenger_streak: number; updated_ts: number } | null }>;
  config: Record<string, unknown>;
  note: string;
};

// ----- experiments --------------------------------------------------------------------------------------

export type ExperimentKind = "backtest" | "validation" | "selector_walk_forward" | "final_test";

export type ExperimentSummary = {
  trades?: number;
  net_return_pct?: number | null;
  expectancy_r?: number | null;
  profit_factor?: number | null;
  max_drawdown_pct?: number | null;
  win_rate_pct?: number | null;
  sharpe?: number | null;
  benchmark_return_pct?: number | null;
  stress?: Record<string, { expectancy_r: number | null; net_return_pct: number | null; trades: number }>;
  bootstrap?: { expectancy_r_ci: [number, number] | null };
  random_baseline?: { beats_pct: number | null } | null;
  regimes?: Record<string, Record<string, number>>;
  notes?: string[];
  version_id?: number;
  version_status?: string;
  verdict?: string;
  verdict_message?: string;
  failed_gates?: string[];
  walk_forward?: {
    oos: Record<string, number | null>;
    windows: { window: number; test: [number, number]; trades: number; net_return_pct: number | null; expectancy_r: number | null; profit_factor: number | null }[];
    profitable_window_ratio: number | null;
    stress_oos_expectancy_r: number | null;
  };
  sensitivity?: { stability: number | null; neighbours: { param: string; value: unknown; base_value: unknown; trades: number; expectancy_r: number | null }[]; base?: Record<string, number | null> };
  windows?: { window: number; test: [number, number]; trades: number; net_return_pct: number | null; eligible_by_regime?: unknown }[];
  single_strategies?: Record<string, { trades: number; expectancy_r: number | null; net_pnl: number }>;
};

export type ExperimentListRow = {
  id: string;
  created_at: number;
  finished_at: number | null;
  kind: ExperimentKind;
  status: "running" | "completed" | "failed";
  label: string | null;
  created_by: string;
  strategies: string[];
  pairs: string[];
  timeframe: string;
  segment: string;
  dataset_id: string | null;
  start_ts: number | null;
  end_ts: number | null;
  verdict: string | null;
  error: string | null;
  uses_test_set: boolean;
  parent_id: string | null;
  summary: ExperimentSummary | null;
};

export type Experiment = ExperimentListRow & {
  hypothesis: string | null;
  request: Record<string, unknown> | null;
  config_hash: string;
  software_version: string;
  seed: number | null;
  data_sources: Record<string, Record<string, number>> | null;
  metrics: Record<string, Metrics> | null;
  gates: GateResult | null;
  report_dir: string | null;
  scenarios: string[];
  files: string[];
};

export type DatasetInfo = {
  id: string;
  exchange: string;
  pairs: string[];
  timeframe: string;
  start_ts: number;
  development_end_ts: number;
  validation_end_ts: number;
  end_ts: number;
  test_fingerprint: string;
  sources: Record<string, unknown>;
  segments: Record<"development" | "validation" | "test", { start: number; end: number }>;
  test_accesses: { ts: number; experiment_id: string; strategies: string[]; reason: string; actor: string }[];
};

// ----- research assistant ---------------------------------------------------------------------------------

export type ResearchStatus = {
  available: boolean;
  problems: string[];
  enabled: boolean;
  model: string | null;
  api_key_present: boolean;
  sdk_installed: boolean;
  limits: string[];
};

export type ResearchNote = {
  id: number;
  created_at: number;
  kind: string;
  source: "claude" | "user" | "system";
  experiment_id: string | null;
  title: string;
  body: string;
  payload: Record<string, unknown> | null;
  status: "recorded" | "pending_approval" | "approved" | "rejected";
  decided_by: string | null;
  decided_at: number | null;
  decision_note: string | null;
};

// ----- dashboard, analytics, logs ------------------------------------------------------------------------

export type Dashboard = {
  bot: { status: EngineStatus; label?: string; session: PaperSession | null; engine?: { healthy: boolean; problems: string[] }; kill_switch: KillSwitchState; last_error: string | null };
  portfolio: PortfolioSnapshot | null;
  returns: { daily_pct?: number | null; weekly_pct?: number | null; monthly_pct?: number | null; since_start_pct?: number | null; basis?: string };
  performance: Metrics | null;
  positions: LivePosition[];
  recent_trades: TradeRow[];
  equity: EquityPoint[];
  engine_pairs: Record<string, PairEngineStatus>;
  regimes: RegimesPayload;
  active_strategies: Record<string, { strategy: string | null; version: string | null; mode: string }>;
  risk: {
    open_risk_pct: number;
    open_risk_limit_pct: number;
    exposure_pct: number;
    exposure_limit_pct: number;
    daily_loss_pct: number;
    daily_loss_limit_pct: number;
    drawdown_pct: number;
    drawdown_limit_pct: number;
    daily_tripped: boolean;
    drawdown_tripped: boolean;
  };
  market: { status: string; websocket: boolean; rest_reachable: boolean | null; rest_error: string | null; series: Freshness[]; tickers: Record<string, Ticker | null> };
  events: { ts: number; pair: string; action: string; accepted: boolean; reason: string; strategy: string | null; regime: string }[];
  orders: Pick<OrderRow, "client_order_id" | "pair" | "side" | "purpose" | "status" | "fill_price" | "filled_qty" | "created_ts" | "filled_ts" | "reason" | "reject_reason">[];
  notice: string;
};

export type AnalyticsRun = { source: "paper" | "backtest"; run_id: string; label: string; created_at: number; timeframe: string; scenarios: string[]; uses_test_set?: boolean };

export type HistBin = { from: number; to: number; count: number };

export type Analytics = {
  source: "paper" | "backtest";
  run_id: string | null;
  scenario?: string;
  empty: boolean;
  message?: string;
  filters?: Record<string, string | null>;
  equity_note?: string | null;
  metrics?: Metrics;
  equity?: EquityPoint[];
  drawdown?: { ts: number; drawdown_pct: number }[];
  exposure?: { ts: number; exposure_pct: number }[];
  daily_pnl?: { day: string; pnl: number }[];
  monthly_returns?: Record<string, number | null>;
  r_distribution?: HistBin[];
  pnl_distribution?: HistBin[];
  wins?: number;
  losses?: number;
  fee_contribution?: { gross_pnl: number; fees: number; net_pnl: number; fees_by_month: Record<string, number> };
  by_strategy?: GroupStats;
  by_regime?: GroupStats;
  by_pair?: GroupStats;
  by_exit_reason?: GroupStats;
  benchmark?: { ts: number; strategy: number; benchmark: number }[] | null;
  sensitivity?: ExperimentSummary["sensitivity"] | null;
  oos_vs_is?: Record<string, Record<string, number | null> | null> | null;
  options?: { pairs: string[]; strategies: string[]; regimes: string[]; timeframes: string[] };
};

export type LogRow = { id: number; ts: number; level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL"; source: string; message: string; context: Record<string, unknown> | null };

export type Diagnostics = {
  health: Health;
  versions: { app: string; software: string; python: string; platform: string };
  config_hash: string;
  databases: Record<"state" | "market", { path: string; bytes: number; schema: number; integrity: string }>;
  data: { series: number; candles: number; gappy_series: string[] };
  market: MarketStatus;
  jobs: Job[];
  research: { experiments: number; failed_experiments: number; datasets: number; test_set_accesses: number; assistant: ResearchStatus };
  paper: { status: EngineStatus; kill_switch: boolean; data_mode: string | null };
  safety: Record<string, string>;
};
