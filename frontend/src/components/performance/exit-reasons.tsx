"use client";

import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import { reasonKind } from "@/components/performance/derive";
import { Empty } from "@/components/shared/states";
import type { Structure } from "@/lib/types";

const PALETTE = ["#1e6fd9", "#0e8a3e", "#cf1f2e", "#f59e0b", "#8b5cf6", "#64748b", "#0891b2", "#db2777"];

/** How closed structures ended: take profit, stop loss, time exit, settlement, by hand. */
export function ExitReasons({ rows }: { rows: Structure[] }) {
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(reasonKind(r.close_reason), (counts.get(reasonKind(r.close_reason)) ?? 0) + 1);
  const data = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name, value]) => ({ name, value }));
  if (!data.length) return <Empty title="No exits yet" />;
  return (
    <ResponsiveContainer width="100%" height={288}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius="58%" outerRadius="82%" paddingAngle={1} stroke="none" isAnimationActive={false}>
          {data.map((d, i) => (
            <Cell key={d.name} fill={PALETTE[i % PALETTE.length]} />
          ))}
        </Pie>
        <Tooltip formatter={(v, name) => [`${v} trade${Number(v) === 1 ? "" : "s"}`, String(name)]} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
        <Legend iconSize={9} wrapperStyle={{ fontSize: 11 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}
