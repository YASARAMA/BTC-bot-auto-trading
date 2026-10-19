// React Query hooks, one per backend resource. Polling intervals reflect how fast each thing
// actually changes; live prices also arrive over the WebSocket.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get, qs } from "./api";
import type { ChartPayload, Health, InventoryRow, Job, MarketStatus, OrderBook, PairsInfo, SettingsPayload, Ticker } from "./types";

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
