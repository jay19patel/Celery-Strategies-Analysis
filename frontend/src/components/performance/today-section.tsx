"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { DayView } from "@/components/journal/day-audit";
import { Stat } from "@/components/shared/stat";
import { Button } from "@/components/ui/button";
import { arrowSigned, pnlClass, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Account, Accounts, Broker, Structure } from "@/lib/types";

export function shiftDay(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** One trading day of one broker: today by default, any day from the calendar or a chart. Today also
 *  shows what is moving now — the account against the day's start, and what is still open. */
export function DaySection({
  broker, date, today, tz, accounts, onDay,
}: {
  broker: Broker;
  date: string;
  today: string;
  tz: string;
  accounts: Accounts | undefined;
  onDay: (day: string) => void;
}) {
  const isToday = date === today;
  const { data: open } = useApi<Structure[]>(isToday ? `/trading/structures?status=open&broker=${broker}` : null, { refreshInterval: 10_000 });
  const raw = accounts?.accounts[broker];
  const account = raw && !("error" in raw) ? (raw as Account) : undefined;
  const startEquity = accounts?.today[broker]?.start_equity ?? null;
  const moved = account && startEquity ? account.equity - startEquity : null;
  const unrealised = (open ?? []).reduce((a, s) => a + (s.live?.pnl ?? 0), 0);
  const halted = isToday ? accounts?.today[broker]?.halted : "";
  const title = new Date(`${date}T00:00:00Z`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="icon-sm" className="rounded-full" onClick={() => onDay(shiftDay(date, -1))} aria-label="Previous day">
          <ChevronLeft />
        </Button>
        <div className="min-w-0">
          <div className="text-[17px] font-semibold leading-tight">{isToday ? `Today, ${title}` : title}</div>
          <div className="text-[12px] text-muted-foreground">{tz} trading day</div>
        </div>
        <Button variant="outline" size="icon-sm" className="rounded-full" onClick={() => onDay(shiftDay(date, 1))} disabled={date >= today} aria-label="Next day">
          <ChevronRight />
        </Button>
        {!isToday && (
          <Button variant="secondary" size="sm" className="rounded-full" onClick={() => onDay(today)}>
            Back to today
          </Button>
        )}
      </div>
      {halted && <p className="rounded-xl bg-loss-soft px-3 py-2 text-[13px] text-loss">Halted for the day: {halted}</p>}
      {isToday && (
        <div className="grid grid-cols-2 gap-2.5 sm:gap-3">
          <Stat
            label="Account today"
            value={moved === null ? "—" : arrowSigned(moved)}
            valueClass={pnlClass(moved)}
            hint={startEquity ? `from ${usd(startEquity)} at the day's start, open positions included` : "no start of day recorded yet"}
          />
          <Stat
            label="Open now"
            value={open?.length ? arrowSigned(unrealised) : "—"}
            valueClass={pnlClass(unrealised)}
            hint={`${open?.length ?? 0} position${open?.length === 1 ? "" : "s"}, if closed now`}
          />
        </div>
      )}
      <DayView date={date} broker={broker} />
    </>
  );
}
