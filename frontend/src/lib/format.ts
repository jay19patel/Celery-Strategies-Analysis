// Number and time formatting, one way everywhere.

export function usd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function signed(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${usd(v, digits)}`;
}

export function price(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: v >= 1000 ? 1 : 2 });
}

export function pct(v: number | null | undefined, digits = 1, fraction = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(fraction ? v * 100 : v).toFixed(digits)}%`;
}

export function signedPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function pnlClass(v: number | null | undefined): string {
  if (!v) return "text-muted-foreground";
  return v > 0 ? "text-profit" : "text-loss";
}

export function time(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function ago(ts: number | null | undefined): string {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function until(ts: number | null | undefined): string {
  if (!ts) return "—";
  const s = ts - Date.now() / 1000;
  if (s <= 0) return "now";
  if (s < 60) return `in ${Math.round(s)}s`;
  if (s < 3600) return `in ${Math.round(s / 60)}m`;
  return `in ${(s / 3600).toFixed(1)}h`;
}

export function hours(h: number | null | undefined): string {
  if (h === null || h === undefined) return "—";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
