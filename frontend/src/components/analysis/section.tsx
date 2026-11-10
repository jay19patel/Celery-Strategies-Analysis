"use client";

// The closed trades of one broker, cut any way: period, strategy, symbol, side. Every view below the
// filters follows them. A day, in the calendar or the chart, opens that day at the top of the page and
// nothing here moves; a trade opens its detail.

import { useMemo, useState } from "react";
import { CalendarDays, Clock, Crosshair, Layers, LineChart, ListX, Scale } from "lucide-react";

import { breakdown, daily, filterTrades, summarise, WEEKDAYS, when, type Filters, type Group, type Side } from "@/components/analysis/derive";
import { BiggestLosses, Breakdown, Chips, HourBars, Kpis, MonthCalendar } from "@/components/analysis/views";
import { PnlHistory } from "@/components/journal/pnl-history";
import { SectionLabel } from "@/components/shared/page";
import { Empty } from "@/components/shared/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Structure } from "@/lib/types";

const RANGES = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "0", label: "All time" },
];
const SIDES: { value: Side; label: string }[] = [
  { value: "long", label: "Long" },
  { value: "short", label: "Short" },
  { value: "credit", label: "Credit (sold options)" },
  { value: "debit", label: "Debit (bought options)" },
];
const SIDE_LABEL: Record<string, string> = Object.fromEntries(SIDES.map((s) => [s.value, s.label]));

export function AnalysisSection({
  trades, tz, labels, date, today, onDay, onOpen,
}: {
  trades: Structure[];
  tz: string;
  date: string;
  today: string;
  labels: Record<string, string>;
  onDay: (day: string) => void;
  onOpen: (s: Structure) => void;
}) {
  const [f, setF] = useState<Filters>({ broker: "", strategies: [], symbols: [], sides: [], days: 0 });
  const set = (change: Partial<Filters>) => setF((x) => ({ ...x, ...change }));
  const toggle = (key: "strategies" | "symbols" | "sides", value: string) =>
    setF((x) => ({ ...x, [key]: (x[key] as string[]).includes(value) ? (x[key] as string[]).filter((v) => v !== value) : [...(x[key] as string[]), value] }));

  const strategies = [...new Set(trades.map((s) => s.strategy))].sort((a, b) => (labels[a] ?? a).localeCompare(labels[b] ?? b));
  const symbols = [...new Set(trades.map((s) => s.underlying))].sort();
  const rows = useMemo(() => filterTrades(trades, f), [trades, f]);
  const s = useMemo(() => summarise(rows, tz), [rows, tz]);
  const byDay = useMemo(() => daily(rows, tz), [rows, tz]);
  const days = useMemo(() => Object.fromEntries(breakdown(rows, (r) => when(r.closed_at ?? 0, tz).day).map((g) => [g.key, g])), [rows, tz]);
  const weekdays = useMemo(() => {
    const by = Object.fromEntries(breakdown(rows, (r) => WEEKDAYS[when(r.closed_at ?? 0, tz).weekday]).map((g) => [g.key, g]));
    return WEEKDAYS.flatMap((w) => (by[w] ? [by[w]] : []));
  }, [rows, tz]);
  const hours = useMemo(() => {
    const by = Object.fromEntries(breakdown(rows, (r) => String(when(r.closed_at ?? 0, tz).hour)).map((g) => [g.key, g]));
    return Array.from({ length: 24 }, (_, h): Group => by[h] ?? { key: String(h), trades: 0, wins: 0, pnl: 0, best: 0, worst: 0 });
  }, [rows, tz]);
  // The calendar shows the open day's month, and follows it when a day in another month is opened.
  const [month, setMonth] = useState(date.slice(0, 7));
  const [shownFor, setShownFor] = useState(date);
  if (shownFor !== date) {
    setShownFor(date);
    setMonth(date.slice(0, 7));
  }
  const labelOf = (r: Structure) => (r.strategy === "custom" || r.strategy === "manual" ? r.label : labels[r.strategy] ?? r.label);

  return (
    <>
      {/* The day above is one subject, the results another: room between them, and the heading kept with its filters. */}
      <div className="flex flex-col gap-3 pt-4 sm:pt-6">
        <SectionLabel title="Results" hint="closed trades after every fee · pick a period, strategy, symbol or side and everything below follows" />
        <Card>
          <CardContent className="space-y-3 py-4">
            <Chips label="Period" single options={RANGES} value={[String(f.days)]} onChange={([v]) => set({ days: Number(v) })} />
            <Chips label="Strategy" options={strategies.map((k) => ({ value: k, label: labels[k] ?? k }))} value={f.strategies} onChange={(v) => set({ strategies: v })} />
            <Chips label="Symbol" options={symbols.map((k) => ({ value: k, label: k }))} value={f.symbols} onChange={(v) => set({ symbols: v })} />
            <Chips label="Side" options={SIDES} value={f.sides} onChange={(v) => set({ sides: v })} />
          </CardContent>
        </Card>
      </div>

      {!rows.length ? (
        <Empty title="No closed trade matches">Widen the period or clear a filter.</Empty>
      ) : (
        <>
          <Kpis s={s} />

          <Card>
            <CardHeader>
              <CardTitle>
                <LineChart className="size-4 text-slate-400" /> P&amp;L day by day
                <span className="text-xs font-normal text-muted-foreground">bars per day, line is the running total · tap a bar to open the day</span>
              </CardTitle>
            </CardHeader>
            <CardContent><PnlHistory daily={byDay} onDay={onDay} /></CardContent>
          </Card>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <Card>
              <CardHeader>
                <CardTitle>
                  <CalendarDays className="size-4 text-slate-400" /> Day by day
                  <span className="text-xs font-normal text-muted-foreground">green made money, red lost it, stronger is more · tap a day to open it above</span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <MonthCalendar month={month} days={days} selected={date} today={today} onMonth={setMonth} onDay={onDay} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>
                  <Clock className="size-4 text-slate-400" /> By weekday and hour
                  <span className="text-xs font-normal text-muted-foreground">when each trade closed, {tz}</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-5">
                <Breakdown rows={weekdays} labelOf={(k) => k} />
                <div>
                  <div className="mb-2 text-[11.5px] font-medium text-muted-foreground">Hour of the day <span className="font-normal">· profit up, loss down</span></div>
                  <HourBars hours={hours} />
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-5 lg:grid-cols-3">
            <Card>
              <CardHeader><CardTitle><Crosshair className="size-4 text-slate-400" /> By strategy</CardTitle></CardHeader>
              <CardContent>
                <Breakdown rows={breakdown(rows, (r) => r.strategy)} labelOf={(k) => labels[k] ?? k} onPick={(k) => toggle("strategies", k)} picked={f.strategies} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle><Layers className="size-4 text-slate-400" /> By symbol</CardTitle></CardHeader>
              <CardContent>
                <Breakdown rows={breakdown(rows, (r) => r.underlying)} labelOf={(k) => k} onPick={(k) => toggle("symbols", k)} picked={f.symbols} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle><Scale className="size-4 text-slate-400" /> By side</CardTitle></CardHeader>
              <CardContent>
                <Breakdown rows={breakdown(rows, (r) => r.kind)} labelOf={(k) => SIDE_LABEL[k] ?? k} onPick={(k) => toggle("sides", k)} picked={f.sides} />
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle><ListX className="size-4 text-slate-400" /> Biggest losses <span className="text-xs font-normal text-muted-foreground">when, what and why · tap one for its detail</span></CardTitle>
            </CardHeader>
            <CardContent><BiggestLosses rows={rows} labelOf={labelOf} onOpen={onOpen} /></CardContent>
          </Card>
        </>
      )}
    </>
  );
}
