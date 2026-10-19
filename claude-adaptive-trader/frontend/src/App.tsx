import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { SkeletonRows } from "./components/ui/Feedback";

// Each page is loaded on first visit, so the charting libraries are fetched only when needed.
const Overview = lazy(() => import("./pages/Overview").then((m) => ({ default: m.Overview })));
const MarketTerminal = lazy(() => import("./pages/MarketTerminal").then((m) => ({ default: m.MarketTerminal })));
const StrategiesPage = lazy(() => import("./pages/Strategies").then((m) => ({ default: m.StrategiesPage })));
const BacktestingPage = lazy(() => import("./pages/Backtesting").then((m) => ({ default: m.BacktestingPage })));
const PaperPage = lazy(() => import("./pages/Paper").then((m) => ({ default: m.PaperPage })));
const PortfolioPage = lazy(() => import("./pages/Portfolio").then((m) => ({ default: m.PortfolioPage })));
const RiskPage = lazy(() => import("./pages/Risk").then((m) => ({ default: m.RiskPage })));
const AnalyticsPage = lazy(() => import("./pages/Analytics").then((m) => ({ default: m.AnalyticsPage })));
const LogsPage = lazy(() => import("./pages/Logs").then((m) => ({ default: m.LogsPage })));
const SettingsPage = lazy(() => import("./pages/Settings").then((m) => ({ default: m.SettingsPage })));

export function App() {
  return (
    <AppShell>
      <Suspense fallback={<SkeletonRows rows={12} />}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/market" element={<MarketTerminal />} />
          <Route path="/strategies" element={<StrategiesPage />} />
          <Route path="/backtesting" element={<BacktestingPage />} />
          <Route path="/paper" element={<PaperPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </AppShell>
  );
}
