// Numbers the old Account page had the server compute, derived here from closed structures.

import type { Structure } from "@/lib/types";

/** The close reason without its numbers: "take profit: +0.21 reached ..." -> "Take profit". */
export function reasonKind(reason: string): string {
  const r = reason.toLowerCase();
  if (r.startsWith("take profit")) return "Take profit";
  if (r.startsWith("stop loss")) return "Stop loss";
  if (r.startsWith("time exit")) return "Time exit";
  if (r.startsWith("settled")) return "Settled at expiry";
  if (r.startsWith("daily loss")) return "Daily loss limit";
  if (r.includes("kill switch")) return "Close all";
  if (r.includes("dashboard")) return "Manual close";
  if (r.startsWith("entry failed")) return "Entry failed";
  const head = reason.split(":")[0].trim();
  return head ? head[0].toUpperCase() + head.slice(1) : "Other";
}

export function holdSeconds(s: Structure): number | null {
  return s.closed_at ? s.closed_at - s.opened_at : null;
}

/** Return on the margin the structure tied up, %. */
export function returnPct(s: Structure): number | null {
  return s.pnl !== null && s.margin > 0 ? (100 * s.pnl) / s.margin : null;
}

export function grossPnl(s: Structure): number {
  return s.entry_cash + s.exit_cash;
}

export interface Tally {
  trades: number;
  wins: number;
  pnl: number;
  fees: number;
  winRate: number | null;
}

export function tally(rows: Structure[]): Tally {
  const wins = rows.filter((r) => (r.pnl ?? 0) > 0).length;
  return {
    trades: rows.length,
    wins,
    pnl: rows.reduce((a, r) => a + (r.pnl ?? 0), 0),
    fees: rows.reduce((a, r) => a + r.fees, 0),
    winRate: rows.length ? (100 * wins) / rows.length : null,
  };
}

/** A duration in seconds, the way a trader says it: 45m, 3h 10m, 2d 4h. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** Today's date in `tz`, as YYYY-MM-DD — the key the backend's daily figures use. */
export function dayKey(tz: string, at: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(at);
}

/** Unix seconds at 00:00 of `date` (YYYY-MM-DD) in `tz`. */
export function dayStart(date: string, tz: string): number {
  const [y, m, d] = date.split("-").map(Number);
  const guess = Date.UTC(y, m - 1, d);
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(new Date(guess))
      .map((p) => [p.type, p.value]),
  );
  const asLocal = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute), Number(parts.second));
  return (guess - (asLocal - guess)) / 1000;
}
