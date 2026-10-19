import { titleCase } from "../../lib/format";
import type { StrategySpec } from "../../lib/types";
import { Field, Input, Switch } from "../ui/Form";

export type ParamValues = Record<string, string | boolean>;

// Editable strategy parameters, built from the strategy's own JSON schema. Only values that
// differ from the defaults are sent, so the backend applies its own validation and defaults.
export function ParamsEditor({ spec, values, onChange }: { spec: StrategySpec; values: ParamValues; onChange: (v: ParamValues) => void }) {
  const props = spec.params_schema.properties ?? {};
  const names = Object.keys(props);
  if (!names.length) return <p className="text-xs text-muted">This strategy has no parameters.</p>;
  const ordered = [...spec.tunable.filter((n) => names.includes(n)), ...names.filter((n) => !spec.tunable.includes(n))];
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-2 md:grid-cols-3">
      {ordered.map((name) => {
        const p = props[name];
        const def = spec.params[name];
        const cur = values[name];
        const tunable = spec.tunable.includes(name);
        if (p.type === "boolean") {
          return (
            <Field key={name} label={<span>{titleCase(name)}{tunable && <span className="ml-1 text-accent">•</span>}</span>}>
              <Switch checked={cur === undefined ? Boolean(def) : Boolean(cur)} onChange={(v) => onChange({ ...values, [name]: v })} />
            </Field>
          );
        }
        return (
          <Field key={name} label={<span title={p.description}>{titleCase(name)}{tunable && <span className="ml-1 text-accent" title="Tunable: varied in the sensitivity analysis">•</span>}</span>}>
            <Input
              type="number"
              step={p.type === "integer" ? 1 : "any"}
              value={cur === undefined ? String(def ?? "") : String(cur)}
              onChange={(e) => onChange({ ...values, [name]: e.target.value })}
            />
          </Field>
        );
      })}
    </div>
  );
}

export function changedParams(spec: StrategySpec, values: ParamValues): Record<string, unknown> {
  const props = spec.params_schema.properties ?? {};
  const out: Record<string, unknown> = {};
  for (const [name, v] of Object.entries(values)) {
    const def = spec.params[name];
    if (typeof v === "boolean") {
      if (v !== def) out[name] = v;
      continue;
    }
    if (v.trim() === "") continue;
    const n = props[name]?.type === "integer" ? parseInt(v, 10) : Number(v);
    if (Number.isFinite(n) && n !== def) out[name] = n;
  }
  return out;
}
