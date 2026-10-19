import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { OctagonX, Radio, Server, WifiOff } from "lucide-react";
import { Badge, type Tone } from "../ui/Badge";
import { useHealth, useMarketStatus } from "../../lib/queries";
import { useLiveState } from "../../lib/live";
import { useStatusExtras } from "./statusExtras";

function Clock() {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const utc = new Date(now).toISOString().slice(11, 19);
  return <span className="num hidden text-xs text-muted xl:inline" title="Coordinated Universal Time">{utc} UTC</span>;
}

export function TopBar({ title, description, onMenu, menuButton }: { title: string; description: string; onMenu: () => void; menuButton: ReactNode }) {
  const health = useHealth();
  const market = useMarketStatus();
  const liveState = useLiveState();
  const extras = useStatusExtras();

  // Market data has its own badge, so the system badge reflects the backend's own components.
  const own = Object.entries(health.data?.components ?? {}).filter(([k]) => k !== "market_data").map(([, c]) => c.status);
  const system = own.includes("error") ? "error" : own.includes("degraded") ? "degraded" : health.data ? "ok" : null;
  const backendTone: Tone = health.isError ? "down" : system === "ok" ? "up" : system === "degraded" ? "warn" : system ? "down" : "neutral";
  const healthTitle = health.data ? Object.entries(health.data.components).map(([k, c]) => `${k}: ${c.status}`).join(" · ") : "";
  const m = market.data;
  const binanceLive = !!m && (m.websocket.connected || m.rest.reachable === true);
  const binanceTone: Tone = !m ? "neutral" : binanceLive && m.status === "ok" ? "up" : binanceLive ? "warn" : "down";
  const binanceLabel = !m ? "Binance …" : m.websocket.connected ? "Binance live" : m.rest.reachable ? "Binance REST" : "Binance offline";

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-bg/85 px-3 backdrop-blur sm:px-5">
      <button type="button" className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-text lg:hidden" onClick={onMenu} aria-label="Open menu">
        {menuButton}
      </button>
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-[15px] font-semibold tracking-tight">{title}</h1>
        <p className="hidden truncate text-[11px] text-muted md:block">{description}</p>
      </div>
      <div className="flex items-center gap-1.5 sm:gap-2">
        <Badge tone="accent" title="All portfolio values are simulated. Real-money trading does not exist in this version.">PAPER</Badge>
        {extras.killSwitch && (
          <Link to="/risk">
            <Badge tone="down" title="Kill switch is ON: no new entries"><OctagonX className="size-3" /> KILL SWITCH</Badge>
          </Link>
        )}
        {extras.botStatus && (
          <Link to="/paper" className="hidden sm:inline-flex">
            <Badge tone={extras.botTone} dot title="Paper trading engine status">Bot {extras.botStatus}</Badge>
          </Link>
        )}
        <Link to="/market" className="hidden sm:inline-flex">
          <Badge tone={binanceTone} title={m?.rest.last_error ?? m?.websocket.last_error ?? "Binance public market data"}>
            {binanceLive ? <Radio className="size-3" /> : <WifiOff className="size-3" />}
            {binanceLabel}
          </Badge>
        </Link>
        <Link to="/logs">
          <Badge tone={backendTone} title={health.isError ? "Backend unreachable" : healthTitle}>
            <Server className="size-3" />
            <span className="hidden md:inline">{health.isError ? "Backend down" : `System ${system ?? "…"}`}</span>
          </Badge>
        </Link>
        <span className={liveState === "open" ? "size-2 rounded-full bg-up" : liveState === "connecting" ? "size-2 animate-pulse rounded-full bg-warn" : "size-2 rounded-full bg-down"} title={`Live connection to backend: ${liveState}`} />
        <Clock />
      </div>
    </header>
  );
}
