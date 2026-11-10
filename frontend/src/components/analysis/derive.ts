// Closed trades, cut every way the Performance page shows them. Pure: trades in, numbers out, so each
// view is tested without a browser and every chart reads the same arithmetic.

import type { Structure } from "@/lib/types";

export type Side = "long" | "short" | "credit" | "debit";

export interface Filters {
  broker: string; // "" all
  strategies: string[]; // [] all
  symbols: string[];
  sides: Side[];
  days: number; // 0 all time
}

export interface Group {
  key: string;
  trades: number;
  wins: number;
  pnl: number;
  best: number;
  worst: number;
}

export interface Summary extends Group {
  winRate: number | null;
  profitFactor: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  bestDay: { day: string; pnl: number } | null;
  worstDay: { day: string; pnl: number } | null;
  greenDays: number;
  redDays: number;
}

const WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const WEEKDAYS = WEEKDAY;

/** YYYY-MM-DD and the hour (0-23) and weekday (0 = Monday) of a unix time, in `tz`. */
export function when(ts: number, tz: string): { day: string; hour: number; weekday: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", weekday: "short" })
      .formatToParts(new Date(ts * 1000))
      .map((p) => [p.type, p.value]),
  );
  return { day: `${parts.year}-${parts.month}-${parts.day}`, hour: Number(parts.hour) % 24, weekday: WEEKDAY.indexOf(parts.weekday) };
}

export function filterTrades(rows: Structure[], f: Filters, now = Date.now() / 1000): Structure[] {
  return rows.filter((s) =>
    s.status === "closed" && s.closed_at != null && s.pnl != null
    && (!f.broker || s.broker === f.broker)
    && (!f.strategies.length || f.strategies.includes(s.strategy))
    && (!f.symbols.length || f.symbols.includes(s.underlying))
    && (!f.sides.length || f.sides.includes(s.kind as Side))
    && (!f.days || s.closed_at >= now - f.days * 86_400),
  );
}

function group(key: string, rows: Structure[]): Group {
  const pnls = rows.map((s) => s.pnl ?? 0);
  return {
    key,
    trades: rows.length,
    wins: pnls.filter((p) => p > 0).length,
    pnl: pnls.reduce((a, p) => a + p, 0),
    best: pnls.length ? Math.max(...pnls) : 0,
    worst: pnls.length ? Math.min(...pnls) : 0,
  };
}

/** Realised P&L per trading day (the day the trade closed), oldest first. */
export function daily(rows: Structure[], tz: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const s of rows) {
    const day = when(s.closed_at ?? 0, tz).day;
    out[day] = (out[day] ?? 0) + (s.pnl ?? 0);
  }
  return out;
}

export function summarise(rows: Structure[], tz: string): Summary {
  const g = group("all", rows);
  const wins = rows.map((s) => s.pnl ?? 0).filter((p) => p > 0);
  const losses = rows.map((s) => s.pnl ?? 0).filter((p) => p <= 0);
  const lost = -losses.reduce((a, p) => a + p, 0);
  const days = Object.entries(daily(rows, tz)).map(([day, pnl]) => ({ day, pnl }));
  const byPnl = [...days].sort((a, b) => b.pnl - a.pnl);
  return {
    ...g,
    winRate: g.trades ? (100 * g.wins) / g.trades : null,
    profitFactor: lost > 0 ? wins.reduce((a, p) => a + p, 0) / lost : null,
    avgWin: wins.length ? wins.reduce((a, p) => a + p, 0) / wins.length : null,
    avgLoss: losses.length ? losses.reduce((a, p) => a + p, 0) / losses.length : null,
    bestDay: byPnl[0] ?? null,
    worstDay: byPnl.length ? byPnl[byPnl.length - 1] : null,
    greenDays: days.filter((d) => d.pnl > 0).length,
    redDays: days.filter((d) => d.pnl < 0).length,
  };
}

/** Trades grouped by a key, the biggest P&L (by size, either sign) first. */
export function breakdown(rows: Structure[], keyOf: (s: Structure) => string): Group[] {
  const groups = new Map<string, Structure[]>();
  for (const s of rows) groups.set(keyOf(s), [...(groups.get(keyOf(s)) ?? []), s]);
  return [...groups.entries()].map(([k, v]) => group(k, v)).sort((a, b) => b.pnl - a.pnl);
}

/** The days of a month (YYYY-MM) laid out in Monday-first weeks; null pads the first and last week. */
export function monthGrid(month: string): (string | null)[] {
  const [y, m] = month.split("-").map(Number);
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const lead = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7;
  const cells: (string | null)[] = [...Array<null>(lead).fill(null), ...Array.from({ length: days }, (_, i) => `${month}-${String(i + 1).padStart(2, "0")}`)];
  while (cells.length % 7) cells.push(null);
  return cells;
}

export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1 + delta, 1)).toISOString().slice(0, 7);
}

/** A colour for a P&L against the largest one shown: green or red, stronger as it grows, light
 *  enough at its strongest for the amount written on it to read. */
export function heat(pnl: number | null, max: number): string {
  if (pnl === null || pnl === 0 || max <= 0) return "var(--muted)";
  const a = 0.12 + 0.43 * Math.min(1, Math.abs(pnl) / max);
  return `color-mix(in srgb, ${pnl > 0 ? "var(--profit)" : "var(--loss)"} ${Math.round(a * 100)}%, transparent)`;
}
