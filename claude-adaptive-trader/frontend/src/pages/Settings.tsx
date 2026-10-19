import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, Lock, RotateCcw, Save, ShieldAlert } from "lucide-react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/Dialog";
import { ErrorState, Notice, SkeletonRows } from "../components/ui/Feedback";
import { Field, Input, Segmented, Switch } from "../components/ui/Form";
import { useToast } from "../components/ui/Toast";
import { ApiError, del, errorMessage, put } from "../lib/api";
import { setTimeZonePref, timeZonePref, titleCase, type TimeZonePref } from "../lib/format";
import { useSettings } from "../lib/queries";

const SECTION_INFO: Record<string, { title: string; description: string }> = {
  risk: { title: "Risk limits", description: "Per-trade risk, aggregate exposure and circuit breakers. Capped by the hard limits on the right." },
  costs: { title: "Trading cost assumptions", description: "Research assumptions, not a verified Binance fee quote. Used by backtests and the paper broker." },
  exchange: { title: "Market data", description: "Pairs, freshness threshold and feed polling. Binance public data only — no keys." },
  regime: { title: "Regime detector", description: "Thresholds of the deterministic market-regime classifier." },
  selector: { title: "Strategy selector", description: "Evidence a strategy needs before the selector may choose it." },
  gates: { title: "Acceptance gates", description: "Preliminary research criteria a strategy must pass on out-of-sample data." },
  backtest: { title: "Backtesting", description: "Walk-forward windows, breaker handling, bootstrap and reproducibility seed." },
  paper: { title: "Paper trading", description: "Loop timing, restart behaviour and historical replay speed." },
  research: { title: "Claude research assistant (optional)", description: "Off by default. It can propose experiments; it can never place orders, change limits or touch the test set." },
};

const UNITS: Record<string, string> = {
  risk_per_trade_pct: "% of equity",
  max_risk_per_trade_pct: "% of equity",
  max_open_risk_pct: "% of equity",
  max_position_pct: "% of equity",
  max_total_exposure_pct: "% of equity",
  daily_loss_limit_pct: "% of equity",
  max_drawdown_pct: "% from peak",
  min_order_notional: "USDT",
  min_stop_distance_pct: "%",
  max_stop_distance_pct: "%",
  max_spread_pct: "%",
  liquidity_max_volume_pct: "% of candle volume",
  fee_pct_per_side: "% per side",
  slippage_pct_per_side: "% per side",
  spread_pct: "% full spread",
  take_profit_trade_through_pct: "%",
  max_participation_pct: "% of candle volume",
  stale_after_seconds: "seconds",
  ticker_poll_seconds: "seconds",
  candle_poll_seconds: "seconds",
  poll_seconds: "seconds",
  history_days: "days",
};

type Draft = Record<string, Record<string, unknown>>;

export function SettingsPage() {
  const q = useSettings();
  const qc = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [confirmReset, setConfirmReset] = useState<string | null>(null);
  const [tz, setTz] = useState<TimeZonePref>(timeZonePref());

  useEffect(() => {
    if (q.data) setDraft({});
  }, [q.data]);

  const changes = useMemo(() => {
    const out: Draft = {};
    if (!q.data) return out;
    for (const [section, values] of Object.entries(draft)) {
      for (const [key, value] of Object.entries(values)) {
        const current = q.data.effective[section]?.[key];
        if (JSON.stringify(current) !== JSON.stringify(value)) (out[section] ??= {})[key] = value;
      }
    }
    return out;
  }, [draft, q.data]);
  const dirty = Object.keys(changes).length > 0;

  if (q.isLoading) return <Card><SkeletonRows rows={10} /></Card>;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const data = q.data;

  const save = async () => {
    setSaving(true);
    setErrors({});
    try {
      await put("/api/settings", { changes });
      toast("success", "Settings saved", "Validated against the configuration rules and hard limits.");
      await qc.invalidateQueries();
    } catch (e) {
      if (e instanceof ApiError && Array.isArray(e.details.errors)) {
        const map: Record<string, string> = {};
        for (const err of e.details.errors as { loc: string; msg: string }[]) map[err.loc] = err.msg;
        setErrors(map);
      }
      toast("error", "Settings were not saved", errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const reset = async (section: string) => {
    try {
      await del(`/api/settings${section === "all" ? "" : `?section=${encodeURIComponent(section)}`}`);
      toast("success", section === "all" ? "All overrides removed" : `${titleCase(section)} reset to the configuration file`);
      setConfirmReset(null);
      await qc.invalidateQueries();
    } catch (e) {
      toast("error", "Reset failed", errorMessage(e));
    }
  };

  const setValue = (section: string, key: string, value: unknown) =>
    setDraft((d) => ({ ...d, [section]: { ...(d[section] ?? {}), [key]: value } }));

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="space-y-4">
        <div className="sticky top-14 z-10 -mx-1 flex flex-wrap items-center gap-2 rounded-xl border border-border bg-bg/90 px-3 py-2 backdrop-blur">
          <span className="text-xs text-muted">
            Config hash <span className="num text-text">{data.config_hash}</span>
            {Object.keys(data.overrides).length > 0 && <> · <Badge tone="warn">{Object.values(data.overrides).reduce((n, s) => n + Object.keys(s).length, 0)} override(s) active</Badge></>}
          </span>
          <div className="ml-auto flex gap-2">
            <Button size="sm" variant="ghost" disabled={!dirty} onClick={() => setDraft({})}>Discard</Button>
            <Button size="sm" variant="primary" icon={<Save className="size-3.5" />} disabled={!dirty} loading={saving} onClick={save}>
              Save {dirty ? `(${Object.values(changes).reduce((n, s) => n + Object.keys(s).length, 0)})` : ""}
            </Button>
          </div>
        </div>
        {Object.keys(errors).length > 0 && (
          <Notice tone="down" icon={<ShieldAlert className="size-3.5" />}>
            {Object.entries(errors).map(([k, v]) => <div key={k}><span className="num">{k || "settings"}</span>: {v}</div>)}
          </Notice>
        )}
        {Object.entries(data.editable).map(([section, keys]) => (
          <Card
            key={section}
            title={SECTION_INFO[section]?.title ?? titleCase(section)}
            subtitle={SECTION_INFO[section]?.description}
            actions={data.overrides[section] ? <Button size="sm" variant="ghost" icon={<RotateCcw className="size-3.5" />} onClick={() => setConfirmReset(section)}>Reset section</Button> : undefined}
          >
            {section === "research" && (
              <Notice tone={data.secrets.anthropic_api_key_present ? "up" : "info"} icon={<KeyRound className="size-3.5" />} className="mb-3">
                {data.secrets.anthropic_api_key_present
                  ? "An Anthropic API key is present in the backend environment (its value is never shown or sent to the browser)."
                  : "No ANTHROPIC_API_KEY in the backend environment. The platform works fully without it; the assistant stays unavailable."}
              </Notice>
            )}
            <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
              {keys.map((key) => {
                const fileValue = data.file[section]?.[key];
                const effective = data.effective[section]?.[key];
                const value = draft[section]?.[key] ?? effective;
                const overridden = data.overrides[section] && key in data.overrides[section];
                const err = errors[`${section}.${key}`];
                return (
                  <SettingField
                    key={key}
                    label={
                      <span className="flex items-center gap-1.5">
                        {titleCase(key)}
                        {overridden && <Badge tone="warn" title={`File value: ${JSON.stringify(fileValue)}`}>override</Badge>}
                      </span>
                    }
                    unit={UNITS[key]}
                    value={value}
                    error={err}
                    onChange={(v) => setValue(section, key, v)}
                  />
                );
              })}
            </div>
          </Card>
        ))}
      </div>

      <div className="space-y-4">
        <Card title="Hard safety limits" icon={<Lock className="size-4" />} subtitle="Compiled into the program. No setting, file or assistant can exceed them.">
          <dl className="space-y-2 text-xs">
            {Object.entries(data.hard_limits).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between gap-2">
                <dt className="text-muted">{titleCase(k)}</dt>
                <dd className="num text-right">{typeof v === "number" ? (k === "positions" ? v : `≤ ${v}%`) : <Badge tone="down">{String(v)}</Badge>}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card title="Display" subtitle="Stored in this browser only">
          <Field label="Time zone for dates and the chart axis" hint="All data is stored in UTC.">
            <Segmented
              value={tz}
              onChange={(v) => {
                setTz(v);
                setTimeZonePref(v);
                void qc.invalidateQueries();
              }}
              options={[{ value: "local", label: "Local time" }, { value: "utc", label: "UTC" }]}
            />
          </Field>
        </Card>
        <Card title="Restore defaults" subtitle="Remove every override and use config.yaml again">
          <Button variant="danger" icon={<RotateCcw className="size-3.5" />} disabled={!Object.keys(data.overrides).length} onClick={() => setConfirmReset("all")}>
            Reset all overrides
          </Button>
        </Card>
      </div>

      <ConfirmDialog
        open={confirmReset !== null}
        onClose={() => setConfirmReset(null)}
        onConfirm={() => confirmReset && void reset(confirmReset)}
        title={confirmReset === "all" ? "Reset all settings?" : `Reset ${titleCase(confirmReset ?? "")}?`}
        confirmLabel="Reset"
        danger
      >
        The values from config.yaml will apply again. The change is logged.
      </ConfirmDialog>
    </div>
  );
}

function SettingField({ label, unit, value, error, onChange }: {
  label: ReactNode;
  unit?: string;
  value: unknown;
  error?: string;
  onChange: (v: unknown) => void;
}) {
  if (typeof value === "boolean") {
    return (
      <Field label={label} error={error}>
        <div className="flex h-9 items-center"><Switch checked={value} onChange={onChange} label={value ? "On" : "Off"} /></div>
      </Field>
    );
  }
  if (Array.isArray(value)) {
    return (
      <Field label={label} hint="Comma separated" error={error}>
        <Input value={value.join(", ")} onChange={(e) => onChange(e.target.value.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean))} />
      </Field>
    );
  }
  if (typeof value === "number") {
    return (
      <Field label={label} hint={unit} error={error}>
        <Input type="number" step="any" value={Number.isFinite(value) ? value : ""} onChange={(e) => onChange(e.target.value === "" ? value : Number(e.target.value))} />
      </Field>
    );
  }
  return (
    <Field label={label} hint={unit} error={error}>
      <Input value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}
