// Formatting helpers. Every timestamp from the backend is UTC milliseconds; it is shown in the
// viewer's time zone or in UTC depending on the preference saved in this browser.

export type TimeZonePref = "local" | "utc";

let tzPref: TimeZonePref = readTz();

function readTz(): TimeZonePref {
  try {
    return localStorage.getItem("cat.tz") === "utc" ? "utc" : "local";
  } catch {
    return "local";
  }
}

export function setTimeZonePref(pref: TimeZonePref): void {
  tzPref = pref;
  try {
    localStorage.setItem("cat.tz", pref);
  } catch {
    /* storage may be unavailable */
  }
}

export function timeZonePref(): TimeZonePref {
  return tzPref;
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtCompact(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return "—";
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: digits }).format(v);
}

export function fmtUsd(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return "—";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${v < 0 ? "-" : ""}$${s}`;
}

export function fmtSignedUsd(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return "—";
  return `${v > 0 ? "+" : ""}${fmtUsd(v, digits)}`;
}

export function fmtPct(v: number | null | undefined, digits = 2, signed = false): string {
  if (!isNum(v)) return "—";
  const s = v.toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function priceDigits(price: number): number {
  const a = Math.abs(price);
  if (a >= 1000) return 2;
  if (a >= 10) return 3;
  if (a >= 1) return 4;
  return 6;
}

export function fmtPrice(v: number | null | undefined): string {
  if (!isNum(v)) return "—";
  return fmtNum(v, priceDigits(v));
}

function parts(): Intl.DateTimeFormatOptions {
  return tzPref === "utc" ? { timeZone: "UTC" } : {};
}

export function fmtDateTime(ms: number | null | undefined, withSeconds = false): string {
  if (!isNum(ms)) return "—";
  const d = new Date(ms);
  const s = d.toLocaleString("en-GB", {
    ...parts(),
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hour12: false,
  });
  return tzPref === "utc" ? `${s} UTC` : s;
}

export function fmtDate(ms: number | null | undefined): string {
  if (!isNum(ms)) return "—";
  return new Date(ms).toLocaleDateString("en-GB", { ...parts(), year: "numeric", month: "short", day: "2-digit" });
}

export function fmtTime(ms: number | null | undefined, withSeconds = true): string {
  if (!isNum(ms)) return "—";
  return new Date(ms).toLocaleTimeString("en-GB", {
    ...parts(),
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hour12: false,
  });
}

export function fmtAgo(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return "never";
  if (seconds < 0) return "just now";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function fmtDuration(minutes: number | null | undefined): string {
  if (!isNum(minutes)) return "—";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / 1440).toFixed(1)}d`;
}

export function toneOf(v: number | null | undefined): "up" | "down" | "flat" {
  if (!isNum(v) || v === 0) return "flat";
  return v > 0 ? "up" : "down";
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
