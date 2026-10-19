// React Query hooks, one per backend resource. Polling intervals reflect how fast each thing
// actually changes; live prices also arrive over the WebSocket.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get, qs } from "./api";
import type {
  Analytics, AnalyticsRun, ChartPayload, Dashboard, DatasetInfo, DbPosition, Decision, Diagnostics, EquityPoint, Experiment, ExperimentListRow, Health,
  InventoryRow, Job, LogRow, MarketStatus, OrderBook, OrderRow, PairsInfo, PaperStatus, RegimesPayload, ResearchNote, ResearchStatus, RiskStatus,
  SelectorPayload, SessionRow, SettingsPayload, StrategyInfo, Ticker, TradeRow,
} from "./types";

export const useHealth = () => useQuery({ queryKey: ["health"], queryFn: ({ signal }) => get<Health>("/api/health", signal), refetchInterval: 10_000 });

export const useMarketStatus = () =>
  useQuery({ queryKey: ["market-status"], queryFn: ({ signal }) => get<MarketStatus>("/api/market/status", signal), refetchInterval: 10_000 });

export const usePairs = () => useQuery({ queryKey: ["pairs"], queryFn: ({ signal }) => get<PairsInfo>("/api/market/pairs", signal), staleTime: 60_000 });

export const useTickers = () =>
  useQuery({
    queryKey: ["tickers"],
    queryFn: ({ signal }) => get<{ tickers: Record<string, Ticker | null> }>("/api/market/tickers", signal),
    refetchInterval: 10_000,
  });

export const useCandles = (pair: string, timeframe: string, exchange: string, limit = 500) =>
  useQuery({
    queryKey: ["candles", exchange, pair, timeframe, limit],
    queryFn: ({ signal }) => get<ChartPayload>(`/api/market/candles${qs({ pair, timeframe, exchange, limit })}`, signal),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
    retry: 1,
  });

export const useOrderBook = (pair: string, enabled: boolean) =>
  useQuery({
    queryKey: ["orderbook", pair],
    queryFn: ({ signal }) => get<OrderBook>(`/api/market/orderbook${qs({ pair, depth: 12 })}`, signal),
    refetchInterval: 4_000,
    enabled,
    retry: 0,
  });

export const useInventory = () =>
  useQuery({
    queryKey: ["inventory"],
    queryFn: ({ signal }) => get<{ series: InventoryRow[]; missing_for_strategies: { pair: string; timeframe: string }[] }>("/api/data/inventory", signal),
    refetchInterval: 30_000,
  });

export const useJobs = () => useQuery({ queryKey: ["jobs"], queryFn: ({ signal }) => get<Job[]>("/api/jobs", signal), refetchInterval: 2_000 });

export const useSettings = () => useQuery({ queryKey: ["settings"], queryFn: ({ signal }) => get<SettingsPayload>("/api/settings", signal) });

// ----- paper trading, portfolio, risk ---------------------------------------------------------------

export const usePaperStatus = () =>
  useQuery({ queryKey: ["paper-status"], queryFn: ({ signal }) => get<PaperStatus>("/api/paper/status", signal), refetchInterval: 3_000 });

export const useDashboard = () =>
  useQuery({ queryKey: ["dashboard"], queryFn: ({ signal }) => get<Dashboard>("/api/dashboard", signal), refetchInterval: 5_000 });

export const useSessions = () =>
  useQuery({ queryKey: ["paper-sessions"], queryFn: ({ signal }) => get<SessionRow[]>("/api/paper/sessions", signal), refetchInterval: 15_000 });

export const useDecisions = (params: { session_id?: string; pair?: string; action?: string; accepted?: boolean; limit?: number }) =>
  useQuery({
    queryKey: ["decisions", params],
    queryFn: ({ signal }) => get<Decision[]>(`/api/paper/decisions${qs(params)}`, signal),
    refetchInterval: 5_000,
    placeholderData: keepPreviousData,
  });

export const usePositions = (status: "open" | "closed" | "all" = "open", session_id?: string) =>
  useQuery({
    queryKey: ["positions", status, session_id],
    queryFn: ({ signal }) => get<DbPosition[]>(`/api/portfolio/positions${qs({ status, session_id })}`, signal),
    refetchInterval: 5_000,
  });

export const useOrders = (status?: string, session_id?: string) =>
  useQuery({
    queryKey: ["orders", status, session_id],
    queryFn: ({ signal }) => get<OrderRow[]>(`/api/portfolio/orders${qs({ status, session_id })}`, signal),
    refetchInterval: 5_000,
  });

export type TradeFilters = { source?: string; run_id?: string; scenario?: string; pair?: string; strategy?: string; regime?: string; timeframe?: string; start?: string; end?: string };

export const useTrades = (filters: TradeFilters, limit = 2000) =>
  useQuery({
    queryKey: ["trades", filters, limit],
    queryFn: ({ signal }) => get<TradeRow[]>(`/api/portfolio/trades${qs({ ...filters, limit })}`, signal),
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  });

export const useEquity = (source?: string, run_id?: string, scenario = "base") =>
  useQuery({
    queryKey: ["equity", source, run_id, scenario],
    queryFn: ({ signal }) => get<{ points: EquityPoint[]; run_id: string | null }>(`/api/portfolio/equity${qs({ source, run_id, scenario, max_points: 1500 })}`, signal),
    refetchInterval: 10_000,
  });

export const useRiskStatus = () =>
  useQuery({ queryKey: ["risk-status"], queryFn: ({ signal }) => get<RiskStatus>("/api/risk/status", signal), refetchInterval: 5_000 });

export const useRiskDecisions = (onlyRejected: boolean) =>
  useQuery({
    queryKey: ["risk-decisions", onlyRejected],
    queryFn: ({ signal }) => get<Decision[]>(`/api/risk/decisions${qs({ only_rejected: onlyRejected, limit: 200 })}`, signal),
    refetchInterval: 8_000,
  });

// ----- strategies, research ------------------------------------------------------------------------------

export const useStrategies = () =>
  useQuery({ queryKey: ["strategies"], queryFn: ({ signal }) => get<StrategyInfo[]>("/api/strategies", signal), refetchInterval: 30_000 });

export const useSelector = () =>
  useQuery({ queryKey: ["selector"], queryFn: ({ signal }) => get<SelectorPayload>("/api/selector", signal), refetchInterval: 30_000 });

export const useRegimes = () =>
  useQuery({ queryKey: ["regimes"], queryFn: ({ signal }) => get<RegimesPayload>("/api/regimes", signal), refetchInterval: 30_000 });

export const useExperiments = (kind?: string) =>
  useQuery({
    queryKey: ["experiments", kind],
    queryFn: ({ signal }) => get<ExperimentListRow[]>(`/api/experiments${qs({ kind, limit: 300 })}`, signal),
    refetchInterval: 10_000,
  });

export const useExperiment = (id: string | null) =>
  useQuery({
    queryKey: ["experiment", id],
    queryFn: ({ signal }) => get<Experiment>(`/api/experiments/${encodeURIComponent(id!)}`, signal),
    enabled: !!id,
  });

export const useExperimentEquity = (id: string | null, scenario: string) =>
  useQuery({
    queryKey: ["experiment-equity", id, scenario],
    queryFn: ({ signal }) => get<{ points: EquityPoint[] }>(`/api/experiments/${encodeURIComponent(id!)}/equity${qs({ scenario })}`, signal),
    enabled: !!id && !!scenario,
  });

export const useExperimentTrades = (id: string | null, scenario: string) =>
  useQuery({
    queryKey: ["experiment-trades", id, scenario],
    queryFn: ({ signal }) => get<TradeRow[]>(`/api/experiments/${encodeURIComponent(id!)}/trades${qs({ scenario, limit: 5000 })}`, signal),
    enabled: !!id && !!scenario,
  });

export const useDatasets = () =>
  useQuery({ queryKey: ["datasets"], queryFn: ({ signal }) => get<DatasetInfo[]>("/api/datasets", signal), refetchInterval: 30_000 });

export const useResearchStatus = () =>
  useQuery({ queryKey: ["research-status"], queryFn: ({ signal }) => get<ResearchStatus>("/api/research/status", signal), refetchInterval: 30_000 });

export const useResearchNotes = () =>
  useQuery({ queryKey: ["research-notes"], queryFn: ({ signal }) => get<ResearchNote[]>("/api/research/notes", signal), refetchInterval: 10_000 });

// ----- analytics, logs ---------------------------------------------------------------------------------------

export const useAnalyticsRuns = () =>
  useQuery({ queryKey: ["analytics-runs"], queryFn: ({ signal }) => get<AnalyticsRun[]>("/api/analytics/runs", signal), refetchInterval: 30_000 });

export const useAnalytics = (params: TradeFilters) =>
  useQuery({
    queryKey: ["analytics", params],
    queryFn: ({ signal }) => get<Analytics>(`/api/analytics${qs(params)}`, signal),
    refetchInterval: 15_000,
    placeholderData: keepPreviousData,
  });

export const useLogs = (params: { level?: string; source?: string; q?: string; limit?: number }, live: boolean) =>
  useQuery({
    queryKey: ["logs", params],
    queryFn: ({ signal }) => get<{ rows: LogRow[]; sources: string[]; counts: Record<string, number> }>(`/api/logs${qs(params)}`, signal),
    refetchInterval: live ? 4_000 : false,
    placeholderData: keepPreviousData,
  });

export const useDiagnostics = () =>
  useQuery({ queryKey: ["diagnostics"], queryFn: ({ signal }) => get<Diagnostics>("/api/diagnostics", signal), refetchInterval: 15_000 });
