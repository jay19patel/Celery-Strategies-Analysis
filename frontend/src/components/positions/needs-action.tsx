"use client";

import { useEffect, useState } from "react";

import { needsAction, type ActionKind, type StrategyError } from "@/components/positions/derive";
import { closeStructure, ExitsDialog } from "@/components/positions/structure-card";
import { Confirm } from "@/components/shared/confirm";
import { SectionLabel } from "@/components/shared/page";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useApi } from "@/lib/live";
import type { Decision, SignalRow, Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

const KIND: Record<ActionKind, { label: string; tone: string }> = {
  stuck: { label: "Stuck", tone: "text-loss" },
  near_stop: { label: "Near stop", tone: "text-loss" },
  error: { label: "Strategy error", tone: "text-loss" },
  near_target: { label: "Near target", tone: "text-profit" },
  expiring: { label: "Expiring", tone: "text-warn" },
};

const ERROR_HOURS = 24;
const SNOOZE_MS = 60 * 60_000;
// shortcut: snoozes live in this browser only, upgrade to a backend field if several people share the desk.
const STORE = "positions.snoozed";

function readSnoozes(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem(STORE) ?? "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

function addSnooze(current: Record<string, number>, id: string): Record<string, number> {
  const now = Date.now();
  const next = Object.fromEntries(Object.entries({ ...current, [id]: now + SNOOZE_MS }).filter(([, t]) => t > now));
  try {
    localStorage.setItem(STORE, JSON.stringify(next));
  } catch {
    // private window: the snooze lasts until the page reloads
  }
  return next;
}

/** The errors the auto-trader, zero-hero and the directional strategies wrote in the last day. */
function useStrategyErrors(now: number): { errors: StrategyError[]; error: unknown } {
  const opts = { refreshInterval: 10_000 };
  const auto = useApi<Decision[]>("/auto/decisions?action=error&limit=10", opts);
  const zh = useApi<Decision[]>("/zerohero/decisions?action=error&limit=10", opts);
  const sig = useApi<SignalRow[]>("/strategies/signals?action=error&limit=10", opts);
  const cutoff = now / 1000 - ERROR_HOURS * 3600;
  const errors = [
    ...(auto.data ?? []).map((d) => ({ id: `auto-${d.id}`, ts: d.ts, source: `Auto options · ${d.underlying}`, text: d.reason })),
    ...(zh.data ?? []).map((d) => ({ id: `zh-${d.id}`, ts: d.ts, source: `Zero-hero · ${d.underlying}`, text: d.reason })),
    ...(sig.data ?? []).map((s) => ({ id: `sig-${s.id}`, ts: s.ts, source: `${s.strategy} · ${s.symbol}`, text: s.reason })),
  ].filter((e) => e.ts >= cutoff);
  return { errors, error: auto.error ?? zh.error ?? sig.error };
}

/** What on the book wants a decision now, with the decision one tap away. */
export function NeedsAction({ open }: { open: Structure[] }) {
  // The dashboard renders only in the browser (after the session check), so storage is there to read.
  const [snoozed, setSnoozed] = useState(readSnoozes);
  const [now, setNow] = useState(Date.now);
  const [editing, setEditing] = useState<Structure | null>(null);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);
  const { errors, error } = useStrategyErrors(now);

  const items = needsAction(open, errors);
  const shown = items.filter((i) => !(snoozed[i.id] > now));
  const quiet = items.length - shown.length;

  return (
    <div className="space-y-2">
      <div className="pb-1.5 pt-1">
        <SectionLabel title="Needs action" hint={shown.length ? `${shown.length}` : undefined} />
      </div>
      <Card className="px-4">
        {error ? (
          // Without the error feeds, "nothing needs you" would be a guess: say so instead.
          <p className="py-3 text-[13px] text-loss">Strategy errors could not be read: {error instanceof Error ? error.message : String(error)}</p>
        ) : null}
        {!shown.length ? (
          <p className="py-3 text-[13px] text-muted-foreground">Nothing needs you right now{quiet ? ` (${quiet} snoozed)` : ""}.</p>
        ) : (
          <ul className="divide-y divide-border">
            {shown.map((item) => {
              const st = item.structure;
              return (
                <li key={item.id} className="flex flex-col gap-2 py-2.5 sm:flex-row sm:items-center">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2 text-[13px]">
                      <span className={cn("shrink-0 text-[11px] font-semibold uppercase tracking-wide", KIND[item.kind].tone)}>{KIND[item.kind].label}</span>
                      <span className="truncate font-medium">{item.title}</span>
                    </div>
                    <div className="truncate text-[12px] text-muted-foreground" title={item.detail}>{item.detail}</div>
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    {st && (
                      <Confirm
                        title={`Close ${st.label}?`}
                        description="Every held leg is closed at the live book now: sold legs are bought back first."
                        confirmLabel="Close"
                        destructive
                        onConfirm={() => closeStructure(st)}
                        trigger={(o) => <Button size="xs" variant="destructive-outline" onClick={o}>Close</Button>}
                      />
                    )}
                    {st && st.instrument === "options" && !st.held_to_settlement && st.status === "open" && (
                      <Button size="xs" variant="outline" onClick={() => setEditing(st)}>Move SL</Button>
                    )}
                    <Confirm
                      title="Snooze for an hour?"
                      description="It leaves this list in this browser for an hour. Nothing changes on the position."
                      confirmLabel="Snooze"
                      onConfirm={() => setSnoozed((s) => addSnooze(s, item.id))}
                      trigger={(o) => <Button size="xs" variant="ghost" onClick={o}>Snooze</Button>}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      {editing && <ExitsDialog key={editing.id} s={editing} open onOpenChange={(o) => !o && setEditing(null)} />}
    </div>
  );
}
