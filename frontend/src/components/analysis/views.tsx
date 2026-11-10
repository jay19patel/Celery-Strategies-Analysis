"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { heat, monthGrid, shiftMonth, WEEKDAYS, type Group, type Summary } from "@/components/analysis/derive";
import { Pnl } from "@/components/shared/money";
import { Stat } from "@/components/shared/stat";
import { Button } from "@/components/ui/button";
import { arrowSigned, dateTime, num, pct, pnlClass } from "@/lib/format";
import type { Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

/** A row of choices that can each be on; none on means all. */
export function Chips<T extends string>({
  label, options, value, onChange, single,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T[];
  onChange: (v: T[]) => void;
  single?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
      <span className="w-full text-[11.5px] font-medium text-muted-foreground sm:w-20 sm:shrink-0">{label}</span>
      {!single && (
        <button
          type="button"
          onClick={() => onChange([])}
          className={cn("rounded-full px-3 py-1 text-[12.5px] font-medium", !value.length ? "bg-primary text-primary-foreground" : "bg-secondary hover:bg-secondary/70")}
        >
          All
        </button>
      )}
      {options.map((o) => {
        const on = value.includes(o.value);
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(single ? [o.value] : on ? value.filter((v) => v !== o.value) : [...value, o.value])}
            className={cn("rounded-full px-3 py-1 text-[12.5px] font-medium", on ? "bg-primary text-primary-foreground" : "bg-secondary hover:bg-secondary/70")}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Kpis({ s }: { s: Summary }) {
  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 sm:gap-3 xl:grid-cols-6">
      <Stat label="Net P&L" value={arrowSigned(s.pnl)} valueClass={pnlClass(s.pnl)} hint={`${s.trades} trades, after fees`} />
      <Stat label="Win rate" value={s.winRate === null ? "—" : pct(s.winRate, 0)} hint={`${s.wins} won · ${s.trades - s.wins} lost`} />
      <Stat label="Profit factor" value={num(s.profitFactor)} valueClass={s.profitFactor !== null && s.profitFactor < 1 ? "text-loss" : undefined} hint="won ÷ lost" />
      <Stat
        label="Average trade"
        value={s.trades ? arrowSigned(s.pnl / s.trades) : "—"}
        valueClass={pnlClass(s.pnl)}
        hint={s.avgWin !== null || s.avgLoss !== null ? `win ${arrowSigned(s.avgWin)} · loss ${arrowSigned(s.avgLoss)}` : undefined}
      />
      <Stat label="Best day" value={s.bestDay ? arrowSigned(s.bestDay.pnl) : "—"} valueClass="text-profit" hint={s.bestDay?.day} />
      <Stat label="Worst day" value={s.worstDay ? arrowSigned(s.worstDay.pnl) : "—"} valueClass="text-loss" hint={s.worstDay ? `${s.worstDay.day} · ${s.greenDays} green / ${s.redDays} red days` : undefined} />
    </div>
  );
}

/** One month, a cell per day coloured by what it made or lost, with the amount and the trade count.
 *  A tap opens that day at the top of the page; the open day is outlined. */
export function MonthCalendar({
  month, days, selected, today, onMonth, onDay,
}: {
  month: string;
  days: Record<string, Group>;
  selected: string;
  today: string;
  onMonth: (month: string) => void;
  onDay: (day: string) => void;
}) {
  const cells = monthGrid(month);
  const inMonth = cells.flatMap((d) => (d && days[d] ? [days[d]] : []));
  const max = Math.max(0, ...inMonth.map((g) => Math.abs(g.pnl)));
  const total = inMonth.reduce((a, g) => a + g.pnl, 0);
  const trades = inMonth.reduce((a, g) => a + g.trades, 0);
  const title = new Date(`${month}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <div className="flex items-center gap-1">
          <Button variant="outline" size="icon-sm" className="rounded-full" onClick={() => onMonth(shiftMonth(month, -1))} aria-label="Previous month"><ChevronLeft /></Button>
          <span className="w-36 text-center text-[15px] font-semibold">{title}</span>
          <Button variant="outline" size="icon-sm" className="rounded-full" onClick={() => onMonth(shiftMonth(month, 1))} disabled={month >= today.slice(0, 7)} aria-label="Next month"><ChevronRight /></Button>
        </div>
        <span className="text-[12.5px] text-muted-foreground">
          <Pnl value={trades ? total : null} className="text-[13px]" /> · {trades} trade{trades === 1 ? "" : "s"} · {inMonth.filter((g) => g.pnl > 0).length} green, {inMonth.filter((g) => g.pnl < 0).length} red days
        </span>
      </div>
      <div className="grid grid-cols-7 gap-1 sm:gap-1.5">
        {WEEKDAYS.map((w) => <div key={w} className="pb-0.5 text-center text-[11px] font-medium text-muted-foreground">{w}</div>)}
        {cells.map((day, i) => {
          if (!day) return <div key={`pad-${i}`} />;
          const g = days[day];
          const future = day > today;
          return (
            <button
              key={day}
              type="button"
              disabled={future}
              onClick={() => onDay(day)}
              aria-current={day === selected ? "date" : undefined}
              title={g ? `${day}: ${arrowSigned(g.pnl)} over ${g.trades} trade${g.trades === 1 ? "" : "s"}` : day}
              style={{ background: g ? heat(g.pnl, max) : undefined }}
              className={cn(
                "flex min-h-14 min-w-0 flex-col justify-between rounded-lg bg-secondary/50 p-1 text-left transition-shadow sm:min-h-[76px] sm:p-2",
                future ? "opacity-40" : "hover:ring-2 hover:ring-primary/40",
                day === selected && "ring-2 ring-primary",
              )}
            >
              <span className={cn("text-[11.5px] font-semibold sm:text-[12.5px]", day === today && "grid size-5 place-items-center rounded-full bg-primary text-[11px] text-primary-foreground sm:size-6")}>
                {Number(day.slice(8))}
              </span>
              {g && (
                <span className="min-w-0 text-right">
                  {/* A phone's cell is ~45px wide: whole dollars there, cents from a tablet up. */}
                  <span className="num block whitespace-nowrap text-[10px] font-semibold sm:hidden">{arrowSigned(g.pnl, Math.abs(g.pnl) >= 10 ? 0 : 1)}</span>
                  <span className="num hidden whitespace-nowrap text-[12px] font-semibold sm:block">{arrowSigned(g.pnl)}</span>
                  <span className="hidden text-[10.5px] opacity-75 sm:block">{g.trades} trade{g.trades === 1 ? "" : "s"}</span>
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** P&L by the hour trades closed: a column per hour, profit up and loss down from the middle line. */
export function HourBars({ hours }: { hours: Group[] }) {
  const max = Math.max(1e-9, ...hours.map((g) => Math.abs(g.pnl)));
  return (
    <div>
      <div className="flex h-36 items-stretch gap-[3px]">
        {hours.map((g, h) => (
          <div
            key={h}
            className="relative flex-1 rounded-sm hover:bg-muted/60"
            title={`${String(h).padStart(2, "0")}:00–${String(h).padStart(2, "0")}:59 — ${g.trades ? `${arrowSigned(g.pnl)} over ${g.trades} trade${g.trades === 1 ? "" : "s"}, ${pct((100 * g.wins) / g.trades, 0)} won` : "no trade closed"}`}
          >
            <span className="absolute inset-x-0 top-1/2 h-px bg-border" />
            {g.trades > 0 && (
              <span
                className={cn("absolute inset-x-0 rounded-sm", g.pnl >= 0 ? "bottom-1/2 bg-profit" : "top-1/2 bg-loss")}
                style={{ height: `${Math.max(2, (50 * Math.abs(g.pnl)) / max)}%` }}
              />
            )}
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-[3px] text-[10px] text-muted-foreground">
        {hours.map((_, h) => <span key={h} className="flex-1 text-center">{h % 3 ? "" : h}</span>)}
      </div>
    </div>
  );
}

/** One bar per group, profit right and loss left of the middle line; a tap filters the page to it. */
export function Breakdown({ rows, labelOf, onPick, picked = [] }: { rows: Group[]; labelOf: (k: string) => string; onPick?: (k: string) => void; picked?: string[] }) {
  if (!rows.length) return <p className="py-6 text-center text-[13px] text-muted-foreground">No trades in this view.</p>;
  const max = Math.max(...rows.map((g) => Math.abs(g.pnl)), 1e-9);
  return (
    <div className="space-y-1">
      {rows.map((g) => (
        <button
          key={g.key}
          type="button"
          disabled={!onPick}
          onClick={() => onPick?.(g.key)}
          className={cn("grid w-full disabled:cursor-default grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_auto] items-center gap-3 rounded-lg px-2 py-1.5 text-left enabled:hover:bg-muted/50", picked.includes(g.key) && "bg-accent")}
        >
          <span className="min-w-0">
            <span className="block text-[13px] font-medium leading-snug">{labelOf(g.key)}</span>
            <span className="block text-[11px] text-muted-foreground">{g.trades} trade{g.trades === 1 ? "" : "s"} · {pct((100 * g.wins) / g.trades, 0)} won</span>
          </span>
          <span className="relative h-3 rounded-full bg-muted">
            <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
            <span
              className={cn("absolute inset-y-0 rounded-full", g.pnl >= 0 ? "left-1/2 bg-profit" : "right-1/2 bg-loss")}
              style={{ width: `${(50 * Math.abs(g.pnl)) / max}%` }}
            />
          </span>
          <Pnl value={g.pnl} className="text-[13px]" />
        </button>
      ))}
    </div>
  );
}

/** The trades that cost the most, with when and why; a tap opens the trade. */
export function BiggestLosses({ rows, labelOf, onOpen }: { rows: Structure[]; labelOf: (s: Structure) => string; onOpen: (s: Structure) => void }) {
  const worst = rows.filter((s) => (s.pnl ?? 0) < 0).sort((a, b) => (a.pnl ?? 0) - (b.pnl ?? 0)).slice(0, 8);
  if (!worst.length) return <p className="py-6 text-center text-[13px] text-muted-foreground">No losing trade in this view.</p>;
  return (
    <div>
      {worst.map((s) => (
        <button
          key={s.id}
          type="button"
          onClick={() => onOpen(s)}
          className="flex w-full flex-col gap-0.5 border-b border-separator px-1 py-2.5 text-left last:border-0 hover:bg-muted/40 sm:flex-row sm:items-center sm:gap-4"
        >
          <span className="num shrink-0 text-[12px] text-muted-foreground sm:w-36">{dateTime(s.closed_at)}</span>
          <span className="shrink-0 text-[13px] font-medium sm:w-56">
            {labelOf(s)} <span className="text-[12px] font-normal text-muted-foreground">{s.underlying} · {s.kind}</span>
          </span>
          <span className="min-w-0 flex-1 text-[12.5px] text-muted-foreground">{s.close_reason}</span>
          <Pnl value={s.pnl} className="shrink-0 text-[13.5px]" />
        </button>
      ))}
    </div>
  );
}
