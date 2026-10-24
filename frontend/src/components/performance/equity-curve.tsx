"use client";

import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { dateTime, signed, titleCase } from "@/lib/format";
import type { Stats } from "@/lib/types";

type Point = Stats["curve"][number];

function CurveTooltip({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border bg-card px-3 py-2 text-xs shadow-md">
      <div className="text-muted-foreground">{dateTime(p.ts)}</div>
      <div className="num mt-1 font-semibold">Total {signed(p.pnl)}</div>
      <div className="num">
        {titleCase(p.strategy)}: <span className={p.trade >= 0 ? "text-profit" : "text-loss"}>{signed(p.trade)}</span>
      </div>
    </div>
  );
}

/** Cumulative realised P&L, green above zero and red below. */
export function EquityCurve({ curve }: { curve: Point[] }) {
  const data = curve.length ? [{ ts: curve[0].ts - 1, pnl: 0, trade: 0, strategy: "start", id: "start" }, ...curve] : [];
  const values = data.map((d) => d.pnl);
  const max = Math.max(0, ...values);
  const min = Math.min(0, ...values);
  const split = max === min ? 1 : max / (max - min);
  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="pnlStroke" x1="0" y1="0" x2="0" y2="1">
            <stop offset={split} stopColor="var(--profit)" />
            <stop offset={split} stopColor="var(--loss)" />
          </linearGradient>
          <linearGradient id="pnlFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset={0} stopColor="var(--profit)" stopOpacity={0.18} />
            <stop offset={split} stopColor="var(--profit)" stopOpacity={0.04} />
            <stop offset={split} stopColor="var(--loss)" stopOpacity={0.04} />
            <stop offset={1} stopColor="var(--loss)" stopOpacity={0.18} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#f1f5f9" vertical={false} />
        <XAxis
          dataKey="ts"
          type="number"
          domain={["dataMin", "dataMax"]}
          tickFormatter={(t: number) => new Date(t * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}
          tick={{ fontSize: 11, fill: "#64748b" }}
          stroke="#e2e8f0"
        />
        <YAxis tick={{ fontSize: 11, fill: "#64748b" }} stroke="#e2e8f0" width={56} tickFormatter={(v: number) => v.toFixed(2)} />
        <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
        <Tooltip content={<CurveTooltip />} />
        <Area type="stepAfter" dataKey="pnl" stroke="url(#pnlStroke)" strokeWidth={2} fill="url(#pnlFill)" isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
