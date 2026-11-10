"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { Bar, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { arrowSigned, usd } from "@/lib/format";

interface Point {
  day: string;
  pnl: number;
  total: number;
}

/** Every calendar day from `first` to `last` inclusive, as YYYY-MM-DD. */
function days(first: string, last: string): string[] {
  const out: string[] = [];
  const d = new Date(`${first}T00:00:00Z`);
  const end = new Date(`${last}T00:00:00Z`).getTime();
  while (d.getTime() <= end && out.length < 3660) {
    out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

const short = (day: string) => new Date(`${day}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

/** Realised P&L from the first trading day to the last: a bar per day and the running total, every
 *  calendar day in between included so quiet stretches read as flat, not skipped. A bar opens its day. */
export function PnlHistory({ daily, onDay }: { daily: Record<string, number>; onDay?: (day: string) => void }) {
  const router = useRouter();
  const open = (day: string) => (onDay ? onDay(day) : router.push(`/performance?date=${day}`));
  const data = useMemo<Point[]>(() => {
    const keys = Object.keys(daily).sort();
    if (!keys.length) return [];
    let total = 0;
    return days(keys[0], keys[keys.length - 1]).map((day) => {
      const pnl = daily[day] ?? 0;
      total += pnl;
      return { day, pnl, total };
    });
  }, [daily]);

  if (!data.length) return <p className="py-10 text-center text-[13px] text-muted-foreground">No closed trades yet: the graph starts with the first one.</p>;
  const last = data[data.length - 1];
  const best = data.reduce((a, p) => (p.pnl > a.pnl ? p : a), data[0]);
  const worst = data.reduce((a, p) => (p.pnl < a.pnl ? p : a), data[0]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-[12.5px]">
        <span className="text-muted-foreground">
          {short(data[0].day)} to {short(last.day)} · {data.length} days
        </span>
        <span>
          Total <b className={`num ${last.total >= 0 ? "text-profit" : "text-loss"}`}>{arrowSigned(last.total)}</b>
        </span>
        <span>
          Best day <b className="num text-profit">{arrowSigned(best.pnl)}</b> <span className="text-muted-foreground">{short(best.day)}</span>
        </span>
        <span>
          Worst day <b className="num text-loss">{arrowSigned(worst.pnl)}</b> <span className="text-muted-foreground">{short(worst.day)}</span>
        </span>
      </div>
      <div className="h-64 sm:h-72">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
            <XAxis dataKey="day" tickFormatter={short} tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} axisLine={false} minTickGap={28} />
            {/* One scale for both: a day's bar and the running total read against the same zero. */}
            <YAxis width={56} tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} axisLine={false} tickFormatter={(v: number) => usd(v, 0)} />
            <ReferenceLine y={0} stroke="var(--chart-text)" strokeOpacity={0.35} />
            <Tooltip
              cursor={{ fill: "var(--muted)", opacity: 0.4 }}
              contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 12 }}
              labelFormatter={(d) => new Date(`${d}T00:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })}
              formatter={(v, name) => [arrowSigned(Number(v)), name === "total" ? "running total" : "that day"]}
            />
            <Bar
              dataKey="pnl"
              maxBarSize={18}
              radius={[3, 3, 3, 3]}
              className="cursor-pointer"
              onClick={(p: { payload?: Point }) => p.payload && open(p.payload.day)}
            >
              {data.map((p) => (
                <Cell key={p.day} fill={p.pnl >= 0 ? "var(--profit)" : "var(--loss)"} fillOpacity={0.55} />
              ))}
            </Bar>
            <Line dataKey="total" type="linear" stroke="var(--primary)" strokeWidth={2} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
