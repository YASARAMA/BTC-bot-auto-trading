import { Tabs } from "../components/ui/Form";
import { usePersistent } from "../lib/usePersistent";
import { ResearchAssistant } from "./strategies/ResearchAssistant";
import { RegimesView, SelectorView } from "./strategies/SelectorAndRegimes";
import { StrategyCards } from "./strategies/StrategyCards";

type Tab = "strategies" | "selector" | "regimes" | "research";

export function StrategiesPage() {
  const [tab, setTab] = usePersistent<Tab>("strategies.tab", "strategies");
  return (
    <div className="space-y-4">
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "strategies", label: "Strategies & versions" },
          { value: "selector", label: "Adaptive selector" },
          { value: "regimes", label: "Market regimes" },
          { value: "research", label: "Research assistant" },
        ]}
      />
      {tab === "strategies" && <StrategyCards />}
      {tab === "selector" && <SelectorView />}
      {tab === "regimes" && <RegimesView />}
      {tab === "research" && <ResearchAssistant />}
    </div>
  );
}
