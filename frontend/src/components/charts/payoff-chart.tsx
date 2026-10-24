"use client";

import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { price, signed } from "@/lib/format";

/** P&L at expiry across settlement prices: green where it pays, red where it loses. */
export function PayoffChart({ curve, spot, breakevens, height = 220 }: { curve: { price: number; pnl: number }[]; spot: number; breakevens: number[]; height?: number }) {
  if (!curve.length) return null;
  const max = Math.max(...curve.map((p) => p.pnl));
  const min = Math.min(...curve.map((p) => p.pnl));
  // Where zero sits in the gradient, so the fill flips colour exactly at breakeven.
  const zero = max <= 0 ? 0 : min >= 0 ? 1 : max / (max - min);
  const data = curve.map((p) => ({ ...p, pnl: Number(p.pnl.toFixed(4)) }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="payoffFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset={0} stopColor="#0e8a3e" stopOpacity={0.28} />
            <stop offset={zero} stopColor="#0e8a3e" stopOpacity={0.06} />
            <stop offset={zero} stopColor="#cf1f2e" stopOpacity={0.06} />
            <stop offset={1} stopColor="#cf1f2e" stopOpacity={0.28} />
          </linearGradient>
          <linearGradient id="payoffLine" x1="0" y1="0" x2="0" y2="1">
            <stop offset={zero} stopColor="#0e8a3e" />
            <stop offset={zero} stopColor="#cf1f2e" />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#f1f5f9" vertical={false} />
        <XAxis dataKey="price" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(v) => price(v)} tick={{ fontSize: 11, fill: "#64748b" }} tickLine={false} axisLine={{ stroke: "#e2e8f0" }} minTickGap={40} />
        <YAxis tickFormatter={(v) => signed(v, Math.abs(max - min) < 5 ? 2 : 0)} tick={{ fontSize: 11, fill: "#64748b" }} tickLine={false} axisLine={false} width={56} />
        <Tooltip
          formatter={(v) => [signed(Number(v), 4), "P&L at expiry"]}
          labelFormatter={(v) => `Settles at ${price(Number(v))}`}
          contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}
        />
        <ReferenceLine y={0} stroke="#94a3b8" />
        <ReferenceLine x={spot} stroke="#1e6fd9" strokeDasharray="4 3" label={{ value: "spot", position: "top", fontSize: 10, fill: "#1e6fd9" }} />
        {breakevens.map((b) => (
          <ReferenceLine key={b} x={b} stroke="#94a3b8" strokeDasharray="2 3" />
        ))}
        <Area type="linear" dataKey="pnl" stroke="url(#payoffLine)" strokeWidth={2} fill="url(#payoffFill)" isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
