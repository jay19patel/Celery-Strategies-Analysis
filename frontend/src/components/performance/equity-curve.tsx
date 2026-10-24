"use client";

import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { dateTime, signed, titleCase, usd } from "@/lib/format";
import type { Stats } from "@/lib/types";

type Point = Stats["curve"][number];

interface Row {
  ts: number;
  value: number;
  trade: Point | null;
}

const UP = "#0e8a3e";
const DOWN = "#cf1f2e";

function CurveTooltip({ active, payload, asEquity }: { active?: boolean; payload?: { payload: Row }[]; asEquity: boolean }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border bg-card px-3 py-2 text-xs shadow-md">
      <div className="text-muted-foreground">{dateTime(p.ts)}</div>
      <div className="num mt-1 font-semibold">{asEquity ? `Equity ${usd(p.value)}` : `Total ${signed(p.value)}`}</div>
      {p.trade ? (
        <div className="num">
          {titleCase(p.trade.strategy)}: <span className={p.trade.trade >= 0 ? "text-profit" : "text-loss"}>{signed(p.trade.trade, 4)}</span>
        </div>
      ) : (
        <div className="text-muted-foreground">{asEquity ? "Account opened" : "Start"}</div>
      )}
    </div>
  );
}

function Dot(props: { cx?: number; cy?: number; payload?: Row; dense: boolean }) {
  const { cx, cy, payload, dense } = props;
  if (cx === undefined || cy === undefined || !payload?.trade || dense) return null;
  return <circle cx={cx} cy={cy} r={3.5} fill={payload.trade.trade < 0 ? DOWN : UP} stroke="#fff" strokeWidth={1.5} />;
}

/** Equity after every closed trade. Each segment is green where the account grew and red where it
 * shrank, each dot is the trade that moved it, and the fill turns red under the starting line. */
export function EquityCurve({ curve, start = 0, opened }: { curve: Point[]; start?: number; opened?: number | null }) {
  if (!curve.length) return null;
  const asEquity = start !== 0;
  const rows: Row[] = [
    { ts: Math.min(opened ?? curve[0].ts - 60, curve[0].ts - 1), value: start, trade: null },
    ...curve.map((p) => ({ ts: p.ts, value: start + p.pnl, trade: p })),
  ];
  const t0 = rows[0].ts;
  const span = rows[rows.length - 1].ts - t0 || 1;
  const values = rows.map((r) => r.value);
  const hi = Math.max(start, ...values);
  const lo = Math.min(start, ...values);
  const water = hi === lo ? 1 : (hi - start) / (hi - lo); // where the starting line sits, top to bottom
  const dense = rows.length > 120;
  const digits = hi - lo < 1 ? 3 : hi - lo < 50 ? 2 : 0; // a tiny range needs decimals, or every tick reads the same

  // A horizontal gradient with a hard stop at every point colours each segment on its own.
  const stops = rows.slice(1).flatMap((r, i) => {
    const color = r.value >= rows[i].value ? UP : DOWN;
    return [
      { offset: (rows[i].ts - t0) / span, color },
      { offset: (r.ts - t0) / span, color },
    ];
  });

  return (
    <ResponsiveContainer width="100%" height={288}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="equitySegments" x1="0" y1="0" x2="1" y2="0">
            {stops.map((s, i) => (
              <stop key={i} offset={s.offset} stopColor={s.color} />
            ))}
          </linearGradient>
          <linearGradient id="equityWater" x1="0" y1="0" x2="0" y2="1">
            <stop offset={0} stopColor={UP} stopOpacity={0.18} />
            <stop offset={water} stopColor={UP} stopOpacity={0.02} />
            <stop offset={water} stopColor={DOWN} stopOpacity={0.02} />
            <stop offset={1} stopColor={DOWN} stopOpacity={0.18} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#f1f5f9" vertical={false} />
        <XAxis
          dataKey="ts"
          type="number"
          domain={["dataMin", "dataMax"]}
          tickFormatter={(t: number) => new Date(t * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}
          tick={{ fontSize: 10, fill: "#64748b" }}
          stroke="#e2e8f0"
          minTickGap={40}
        />
        <YAxis
          domain={[lo, hi]}
          tick={{ fontSize: 10, fill: "#64748b" }}
          stroke="#e2e8f0"
          width={64}
          tickFormatter={(v: number) => (asEquity ? `$${usd(v, digits)}` : signed(v, digits))}
        />
        <ReferenceLine y={start} stroke="#94a3b8" strokeDasharray="3 3" />
        <Tooltip content={<CurveTooltip asEquity={asEquity} />} />
        <Area type="linear" dataKey="value" baseValue={start} stroke="none" fill="url(#equityWater)" isAnimationActive={false} />
        <Line
          type="linear"
          dataKey="value"
          stroke="url(#equitySegments)"
          strokeWidth={2}
          dot={(p: { cx?: number; cy?: number; payload?: Row; index?: number }) => <Dot key={p.index} cx={p.cx} cy={p.cy} payload={p.payload} dense={dense} />}
          activeDot={{ r: 6 }}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
