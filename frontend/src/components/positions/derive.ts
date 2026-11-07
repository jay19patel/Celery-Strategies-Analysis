// What an open structure is worth if closed now, taken apart: the move, the fees and the spread.

import { arrowSigned } from "@/lib/format";
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

// What on the book wants a decision. Thresholds here, in one place.
export const NEAR = 0.8; // this share of the way to a stop or take profit
export const EXPIRY_HOURS = 24;

export type ActionKind = "stuck" | "near_stop" | "error" | "near_target" | "expiring";

export interface ActionItem {
  /** stable across refreshes, so a snooze sticks to it */
  id: string;
  kind: ActionKind;
  title: string;
  detail: string;
  structure?: Structure;
}

export interface StrategyError {
  id: string;
  source: string;
  text: string;
}

const ORDER: Record<ActionKind, number> = { stuck: 0, near_stop: 1, error: 2, near_target: 3, expiring: 4 };

/** How far a perpetual's mark has gone from entry towards `level`: 1 is there. */
function towards(st: Structure, level: number | null): number | null {
  const leg = st.legs[0];
  if (!leg?.mark || !leg.entry_price || level === null || level === leg.entry_price) return null;
  return (leg.mark - leg.entry_price) / (level - leg.entry_price);
}

/** One item per structure (the most urgent reason), then strategy errors, most urgent first. A capped
 *  credit held to settlement settles on its own and is never listed unless it is stuck. */
export function needsAction(open: Structure[], errors: StrategyError[]): ActionItem[] {
  const out: ActionItem[] = [];
  for (const st of open) {
    const add = (kind: ActionKind, detail: string) => out.push({ id: `${st.id}:${kind}`, kind, title: st.label, detail, structure: st });
    if (st.status === "error") {
      add("stuck", st.error || "an order did not resolve; nothing new opens on this broker until it is closed");
      continue;
    }
    if (st.held_to_settlement) continue;
    const pnl = st.live?.pnl ?? null;
    let near: [ActionKind, string] | null = null;
    if (st.instrument === "perp") {
      const s = towards(st, st.stop_price);
      const t = towards(st, st.target_price);
      if (s !== null && s >= NEAR) near = ["near_stop", `${Math.round(100 * s)}% of the way to its stop`];
      else if (t !== null && t >= NEAR) near = ["near_target", `${Math.round(100 * t)}% of the way to its target`];
    } else if (pnl !== null) {
      const sl = st.stop_loss_usd;
      const tp = st.take_profit_usd;
      if (sl !== null && sl < 0 && pnl <= NEAR * sl) near = ["near_stop", `${arrowSigned(pnl)} against a stop at ${arrowSigned(sl)}`];
      else if (tp !== null && tp > 0 && pnl >= NEAR * tp) near = ["near_target", `${arrowSigned(pnl)} against a target at ${arrowSigned(tp)}`];
    }
    if (near) add(...near);
    else if (st.hours_left < EXPIRY_HOURS) add("expiring", `${st.instrument === "perp" ? "max hold ends" : "expires"} in ${st.hours_left.toFixed(1)}h`);
  }
  for (const e of errors) out.push({ id: `err:${e.id}`, kind: "error", title: e.source, detail: e.text });
  return out.sort((a, b) => ORDER[a.kind] - ORDER[b.kind]);
}
