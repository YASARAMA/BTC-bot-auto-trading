import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { MarketTerminal } from "./pages/MarketTerminal";
import { SettingsPage } from "./pages/Settings";
import { Pending } from "./pages/Pending";

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Pending name="Overview" />} />
        <Route path="/market" element={<MarketTerminal />} />
        <Route path="/strategies" element={<Pending name="Strategy Management" />} />
        <Route path="/backtesting" element={<Pending name="Backtesting Laboratory" />} />
        <Route path="/paper" element={<Pending name="Paper Trading" />} />
        <Route path="/portfolio" element={<Pending name="Portfolio & History" />} />
        <Route path="/risk" element={<Pending name="Risk Management" />} />
        <Route path="/analytics" element={<Pending name="Performance Analytics" />} />
        <Route path="/logs" element={<Pending name="Logs & Diagnostics" />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
