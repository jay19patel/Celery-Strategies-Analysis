// What an open structure is worth if closed now, taken apart: the move, the fees and the spread.

import type { Structure } from "@/lib/types";

export interface OpenCosts {
  /** P&L if every held leg closed now: after fees paid, exit fees and the spread */
  net: number | null;
  /** the market's move alone, at the marks, before any fee */
  gross: number | null;
  /** fees already charged */
  paid: number;
  /** fees closing now would charge */
  exit: number | null;
  /** what crossing the bid/ask on the way out costs against the marks */
  spread: number | null;
}

export function openCosts(s: Structure): OpenCosts {
  const live = s.live;
  const paid = live?.fees_paid ?? s.fees;
  const net = live?.pnl ?? null;
  const mid = live?.pnl_mid ?? null; // at the marks, net of fees paid only
  const exit = live?.exit_fees ?? null;
  return {
    net,
    gross: mid === null ? null : mid + paid,
    paid,
    exit,
    spread: mid === null || net === null || exit === null ? null : Math.max(0, mid - net - exit),
  };
}

/** The same parts summed over several structures; a part is null when any structure lacks it. */
export function sumCosts(rows: Structure[]): OpenCosts {
  const parts = rows.map(openCosts);
  const total = (k: keyof OpenCosts): number | null =>
    parts.some((p) => p[k] === null) ? null : parts.reduce((a, p) => a + (p[k] ?? 0), 0);
  return { net: total("net"), gross: total("gross"), paid: total("paid") ?? 0, exit: total("exit"), spread: total("spread") };
}
