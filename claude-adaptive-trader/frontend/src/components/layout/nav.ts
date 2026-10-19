import {
  Activity,
  ChartCandlestick,
  ChartColumn,
  CirclePlay,
  FlaskConical,
  Layers,
  LayoutDashboard,
  Settings,
  ShieldCheck,
  Wallet,
  type LucideIcon,
} from "lucide-react";

export type NavItem = { path: string; label: string; short: string; icon: LucideIcon; description: string };

export const NAV: NavItem[] = [
  { path: "/", label: "Overview", short: "Overview", icon: LayoutDashboard, description: "Simulated portfolio, regime, risk and system status at a glance" },
  { path: "/market", label: "Market Terminal", short: "Market", icon: ChartCandlestick, description: "Binance candles, indicators, live prices and order book" },
  { path: "/strategies", label: "Strategy Management", short: "Strategies", icon: Layers, description: "Strategies, versions, evidence, approvals and the selector" },
  { path: "/backtesting", label: "Backtesting Laboratory", short: "Backtests", icon: FlaskConical, description: "Backtests, walk-forward, cost stress and acceptance gates" },
  { path: "/paper", label: "Paper Trading", short: "Paper", icon: CirclePlay, description: "Continuous simulated trading on public market data" },
  { path: "/portfolio", label: "Portfolio & History", short: "Portfolio", icon: Wallet, description: "Positions, orders and every completed paper trade" },
  { path: "/risk", label: "Risk Management", short: "Risk", icon: ShieldCheck, description: "Limits, utilisation, circuit breakers and the kill switch" },
  { path: "/analytics", label: "Performance Analytics", short: "Analytics", icon: ChartColumn, description: "Equity, drawdown, distributions and comparisons from stored results" },
  { path: "/logs", label: "Logs & Diagnostics", short: "Logs", icon: Activity, description: "Structured logs, health checks and data diagnostics" },
  { path: "/settings", label: "Settings", short: "Settings", icon: Settings, description: "Validated configuration within hard safety limits" },
];
