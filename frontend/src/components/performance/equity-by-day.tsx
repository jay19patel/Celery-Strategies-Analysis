"use client";

import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { usd } from "@/lib/format";
import type { EquityPoint } from "@/lib/types";

/** The account's equity at the start of each trading day: open positions included, unlike the
 *  realised curve, so it shows what the account was really worth. */
export function EquityByDay({ rows }: { rows: EquityPoint[] }) {
  return (
    <div className="h-52">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
          <XAxis dataKey="day" tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis width={64} domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} axisLine={false} tickFormatter={(v: number) => usd(v, 0)} />
          <Tooltip formatter={(v) => [usd(Number(v)), "equity at the day's start"]} contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 12 }} />
          <Line dataKey="equity" type="monotone" stroke="var(--primary)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
