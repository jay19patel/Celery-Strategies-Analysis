"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { DayPanel } from "@/components/journal/day-panel";
import { PageHeader } from "@/components/shared/page";
import { LoadError, Loading } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { pnlClass, signed } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { JournalMonth } from "@/lib/types";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function iso(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function today(): { year: number; month: number; date: string } {
  const d = new Date();
  return { year: d.getFullYear(), month: d.getMonth() + 1, date: iso(d.getFullYear(), d.getMonth() + 1, d.getDate()) };
}

export default function JournalPage() {
  const now = today();
  const [ym, setYm] = useState({ year: now.year, month: now.month });
  const [selected, setSelected] = useState(now.date);
  const { data, error, isLoading } = useApi<JournalMonth>(`/portfolio/journal?year=${ym.year}&month=${ym.month}`);

  const shift = (delta: number) => {
    const m = ym.month + delta;
    setYm({ year: ym.year + Math.floor((m - 1) / 12), month: ((m - 1 + 12) % 12) + 1 });
  };

  const daysInMonth = new Date(ym.year, ym.month, 0).getDate();
  const lead = (new Date(ym.year, ym.month - 1, 1).getDay() + 6) % 7; // Monday first
  const cells: (number | null)[] = [...Array(lead).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => i + 1)];
  while (cells.length % 7) cells.push(null);
  const title = new Date(ym.year, ym.month - 1, 1).toLocaleDateString("en-GB", { month: "long", year: "numeric" });

  return (
    <>
      <PageHeader title="Journal" description={`Each trading day's realised P&L and what the auto-trader decided${data ? ` (${data.timezone})` : ""}.`} />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Card className="h-fit">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="icon-sm" onClick={() => shift(-1)} aria-label="Previous month">
                <ChevronLeft />
              </Button>
              <CardTitle className="w-36 justify-center">{title}</CardTitle>
              <Button variant="outline" size="icon-sm" onClick={() => shift(1)} aria-label="Next month">
                <ChevronRight />
              </Button>
            </div>
            {data && (
              <div className="num flex flex-wrap gap-4 text-xs">
                <span>P&L <b className={pnlClass(data.pnl)}>{signed(data.pnl)}</b></span>
                <span className="text-muted-foreground">{data.trades} trades · {data.wins} won</span>
              </div>
            )}
          </CardHeader>
          <CardContent>
            <LoadError error={error} />
            {isLoading && !data ? (
              <Loading rows={5} />
            ) : (
              <div className="grid grid-cols-7 gap-1.5">
                {WEEKDAYS.map((w) => (
                  <div key={w} className="pb-1 text-center text-[11px] font-medium text-muted-foreground">{w}</div>
                ))}
                {cells.map((day, i) => {
                  if (day === null) return <div key={`blank-${i}`} />;
                  const date = iso(ym.year, ym.month, day);
                  const d = data?.days[date];
                  const pnl = d?.pnl ?? 0;
                  return (
                    <button
                      key={date}
                      type="button"
                      onClick={() => setSelected(date)}
                      className={cn(
                        "flex min-h-16 flex-col items-start rounded-lg border p-1.5 text-left transition-colors hover:border-slate-300 sm:min-h-20 sm:p-2",
                        pnl > 0 && "border-green-100 bg-profit-soft/70",
                        pnl < 0 && "border-red-100 bg-loss-soft/70",
                        date === selected && "ring-2 ring-primary/60",
                        date === now.date && "border-primary/50",
                      )}
                    >
                      <span className="num text-[11px] font-semibold text-slate-500">{day}</span>
                      {d && (d.trades > 0 || pnl !== 0) && (
                        <span className={cn("num mt-auto truncate text-[11px] font-semibold sm:text-xs", pnlClass(pnl))}>{signed(pnl)}</span>
                      )}
                      {d && (d.trades > 0 || d.decisions > 0) && (
                        <span className="num hidden truncate text-[10px] text-muted-foreground sm:block">
                          {d.trades ? `${d.trades}t` : ""}{d.trades && d.decisions ? " · " : ""}{d.decisions ? `${d.decisions} dec` : ""}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
        <DayPanel date={selected} />
      </div>
    </>
  );
}
