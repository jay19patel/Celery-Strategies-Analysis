"use client";

import { useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";

import { DayAudit } from "@/components/journal/day-audit";
import { LoadError, Loading } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { pct, pnlClass, signed, signedPct, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Accounts, JournalMonth } from "@/lib/types";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function iso(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function Tile({ label, value, hint, className }: { label: string; value: string; hint: string; className?: string }) {
  return (
    <div className="px-4 py-3">
      <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={cn("num text-[17px] font-semibold", className)}>{value}</div>
      <div className="text-[10.5px] leading-snug text-muted-foreground">{hint}</div>
    </div>
  );
}

function MonthSummary({ m, accounts, label }: { m: JournalMonth; accounts: Accounts | undefined; label: string }) {
  const days = Object.values(m.days);
  const active = days.filter((d) => d.trades > 0);
  const green = active.filter((d) => d.pnl > 0).length;
  const red = active.filter((d) => d.pnl < 0).length;
  const decisions = days.reduce((a, d) => a + d.decisions, 0);
  const opened = days.reduce((a, d) => a + d.opened, 0);
  const paper = accounts?.accounts.paper;
  const account = paper && !("error" in paper) ? paper : null;
  const invested = account && typeof account.extra.starting_balance === "number" ? account.extra.starting_balance : null;
  const sorted = [...active].sort((a, b) => b.pnl - a.pnl);
  return (
    <div>
      <Card className="grid grid-cols-2 divide-x divide-slate-100 md:grid-cols-3 xl:grid-cols-6">
        <Tile label={`${label} P&L`} value={signed(m.pnl)} className={pnlClass(m.pnl)} hint={`${m.trades} closed · realised only`} />
        <Tile label="Win rate" value={m.trades ? pct((100 * m.wins) / m.trades, 0) : "—"} hint={`${m.wins} won · ${m.trades - m.wins} lost`} />
        <Tile label="Days" value={`${active.length} active`} hint={`${green} green · ${red} red`} />
        <Tile
          label="Best · worst day"
          value={sorted.length ? signed(sorted[0].pnl) : "—"}
          className={sorted.length ? pnlClass(sorted[0].pnl) : undefined}
          hint={sorted.length > 1 ? `worst ${signed(sorted[sorted.length - 1].pnl)}` : "one day or none"}
        />
        <Tile label="Auto-trader" value={`${opened} opened`} hint={`${decisions} decisions this month`} />
        <Tile
          label="Paper account"
          value={account ? usd(account.equity) : "—"}
          className={account && invested ? pnlClass(account.equity - invested) : undefined}
          hint={account && invested ? `${signedPct(((account.equity - invested) / invested) * 100)} all time on ${usd(invested, 0)} put in` : "paper is not active"}
        />
      </Card>
      <p className="px-3 pt-1.5 text-[10.5px] text-muted-foreground">
        P&amp;L here is realised — closed structures, in {m.timezone} days. What the account is worth also carries anything still open,
        which is why the two differ while a structure is live.
      </p>
    </div>
  );
}

export default function JournalPage() {
  const [now] = useState(() => new Date());
  const todayStr = iso(now.getFullYear(), now.getMonth() + 1, now.getDate());
  const [ym, setYm] = useState({ year: now.getFullYear(), month: now.getMonth() + 1 });
  const [selected, setSelected] = useState<string | null>(null);
  const { data, error, isLoading } = useApi<JournalMonth>(`/portfolio/journal?year=${ym.year}&month=${ym.month}`, { refreshInterval: 30_000 });
  const { data: accounts } = useApi<Accounts>("/portfolio/accounts");

  const shift = (delta: number) => {
    const m = ym.month + delta;
    setYm({ year: ym.year + Math.floor((m - 1) / 12), month: ((((m - 1) % 12) + 12) % 12) + 1 });
  };
  const daysInMonth = new Date(ym.year, ym.month, 0).getDate();
  const lead = new Date(ym.year, ym.month - 1, 1).getDay(); // Sunday first
  const cells: (number | null)[] = [...Array<null>(lead).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => i + 1)];
  while (cells.length % 7) cells.push(null);
  const label = new Date(ym.year, ym.month - 1, 1).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
  const monthName = new Date(ym.year, ym.month - 1, 1).toLocaleDateString("en-GB", { month: "long" });

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-lg font-bold text-slate-800">
            <CalendarDays className="size-5 text-primary" /> Trade journal &amp; daily audit
          </h1>
          <p className="mt-0.5 text-xs text-slate-500">Click any day for its full audit: what opened and closed, every order, why the auto-trader stood aside, and what went wrong.</p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border bg-card px-3 py-1.5 shadow-xs">
          <Button variant="ghost" size="icon-sm" onClick={() => shift(-1)} aria-label="Previous month">
            <ChevronLeft />
          </Button>
          <span className="w-36 text-center text-sm font-semibold text-slate-700">{label}</span>
          <Button variant="ghost" size="icon-sm" onClick={() => shift(1)} aria-label="Next month">
            <ChevronRight />
          </Button>
          <Button variant="outline" size="xs" className="ml-2" onClick={() => setYm({ year: now.getFullYear(), month: now.getMonth() + 1 })}>
            Today
          </Button>
        </div>
      </div>

      <LoadError error={error} />
      {data && <MonthSummary m={data} accounts={accounts} label={monthName} />}

      <Card className="overflow-hidden">
        <div className="grid grid-cols-7 border-b bg-slate-50">
          {WEEKDAYS.map((w) => (
            <div key={w} className="border-r border-slate-200 py-2.5 text-center text-xs font-bold uppercase tracking-wider text-slate-500 last:border-0">{w}</div>
          ))}
        </div>
        {isLoading && !data ? (
          <Loading rows={6} className="p-4" />
        ) : (
          <div className="grid grid-cols-7 gap-px bg-slate-200">
            {cells.map((day, i) => {
              if (day === null) return <div key={`blank-${i}`} className="min-h-[88px] bg-slate-50 sm:min-h-[110px]" />;
              const date = iso(ym.year, ym.month, day);
              const d = data?.days[date];
              const isToday = date === todayStr;
              const active = !!d && (d.trades > 0 || d.decisions > 0);
              return (
                <button
                  key={date}
                  type="button"
                  onClick={() => setSelected(date)}
                  className={cn("flex min-h-[88px] flex-col bg-card p-2 text-left transition-colors hover:bg-slate-50 sm:min-h-[110px] sm:p-2.5", isToday && "bg-blue-50/40")}
                >
                  <div className="flex items-start justify-between">
                    <span className={cn("text-xs font-bold text-slate-700", isToday && "grid size-7 place-items-center rounded-full bg-primary text-white shadow-sm ring-2 ring-blue-200")}>{day}</span>
                    {active && <span className="size-1.5 rounded-full bg-primary" />}
                  </div>
                  <div className="flex-1" />
                  <div className="text-right">
                    {d && d.trades > 0 ? (
                      <>
                        <span
                          className={cn(
                            "num inline-block rounded px-1.5 py-0.5 text-[11px] font-bold",
                            d.pnl > 0 ? "bg-profit-soft text-profit" : d.pnl < 0 ? "bg-loss-soft text-loss" : "bg-slate-100 text-slate-500",
                          )}
                        >
                          {signed(d.pnl)}
                        </span>
                        <div className="mt-0.5 text-[10px] font-medium text-slate-400">{d.trades} {d.trades === 1 ? "trade" : "trades"}</div>
                      </>
                    ) : d && d.decisions > 0 ? (
                      <span className="num inline-block rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">{d.decisions} decisions</span>
                    ) : (
                      <div className="pt-2 text-[10px] text-slate-300">—</div>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </Card>

      <DayAudit date={selected} onClose={() => setSelected(null)} />
    </>
  );
}
