"use client";

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Pnl } from "@/components/shared/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ago, hours, num, pct, signed, titleCase, usd } from "@/lib/format";
import type { Stats, Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

type Row = Stats["by_strategy"][number];

/** One bar per strategy, green above water and red below, then the numbers behind each. A row
 * click filters the trade history to that strategy. */
export function StrategySection({ rows, closed, selected, onSelect }: { rows: Row[]; closed: Structure[]; selected: string; onSelect: (s: string) => void }) {
  const totalAbs = closed.reduce((a, s) => a + Math.abs(s.pnl ?? 0), 0) || 1;
  const extra = (strategy: string) => {
    const mine = closed.filter((s) => s.strategy === strategy);
    return {
      share: (100 * mine.reduce((a, s) => a + Math.abs(s.pnl ?? 0), 0)) / totalAbs,
      last: mine.reduce<number | null>((a, s) => (s.closed_at && (!a || s.closed_at > a) ? s.closed_at : a), null),
    };
  };
  const chart = rows.map((r) => ({ label: titleCase(r.strategy), pnl: r.pnl, hint: `${r.trades} trades · ${pct(r.win_rate)} won · fees ${usd(r.fees, 4)}` }));

  return (
    <>
      {chart.length > 0 && (
        <div className="px-4 pt-3" style={{ height: Math.max(120, 34 * chart.length + 30) }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart} layout="vertical" margin={{ top: 0, right: 12, left: 0, bottom: 0 }}>
              <XAxis type="number" tick={{ fontSize: 10, fill: "#64748b" }} stroke="#e2e8f0" tickFormatter={(v: number) => signed(v, 2)} />
              <YAxis type="category" dataKey="label" tick={{ fontSize: 11, fill: "#334155" }} width={120} stroke="#e2e8f0" />
              <Tooltip
                cursor={{ fill: "#f8fafc" }}
                contentStyle={{ fontSize: 12, borderRadius: 8 }}
                formatter={(v, _n, item) => [`${signed(Number(v), 4)} net`, (item.payload as { hint: string }).hint]}
              />
              <Bar dataKey="pnl" radius={3} maxBarSize={24} isAnimationActive={false}>
                {chart.map((c) => (
                  <Cell key={c.label} fill={c.pnl >= 0 ? "#0e8a3e" : "#cf1f2e"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Strategy</TableHead>
            <TableHead className="text-right">Trades</TableHead>
            <TableHead className="text-right">Win rate</TableHead>
            <TableHead className="text-right">Net P&L</TableHead>
            <TableHead className="text-right">Avg win · avg loss</TableHead>
            <TableHead className="text-right">Expectancy</TableHead>
            <TableHead className="text-right">Profit factor</TableHead>
            <TableHead className="text-right">Best · worst</TableHead>
            <TableHead className="text-right">Avg hold</TableHead>
            <TableHead className="text-right">Fees</TableHead>
            <TableHead>Last trade</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => {
            const x = extra(r.strategy);
            return (
              <TableRow key={r.strategy} className={cn("cursor-pointer", selected === r.strategy && "bg-accent hover:bg-accent")} onClick={() => onSelect(selected === r.strategy ? "" : r.strategy)}>
                <TableCell>
                  <div className="font-medium">{titleCase(r.strategy)}</div>
                  <div className="mt-1 h-1.5 w-32 overflow-hidden rounded-full bg-slate-100" title={`${num(x.share, 1)}% of all P&L moved`}>
                    <div className={cn("h-full", r.pnl >= 0 ? "bg-profit" : "bg-loss")} style={{ width: `${Math.min(100, x.share)}%` }} />
                  </div>
                </TableCell>
                <TableCell className="num text-right">
                  {r.trades}
                  <div className="text-[10.5px] text-muted-foreground">{r.wins}W · {r.losses}L</div>
                </TableCell>
                <TableCell className="num text-right">{pct(r.win_rate)}</TableCell>
                <TableCell className="text-right"><Pnl value={r.pnl} digits={4} /></TableCell>
                <TableCell className="num text-right">
                  <div className="text-profit">{r.avg_win === null ? "—" : signed(r.avg_win, 4)}</div>
                  <div className="text-loss">{r.avg_loss === null ? "—" : signed(r.avg_loss, 4)}</div>
                </TableCell>
                <TableCell className="text-right">
                  <Pnl value={r.expectancy} digits={4} />
                  <div className="text-[10.5px] text-muted-foreground">per trade</div>
                </TableCell>
                <TableCell className="num text-right">{num(r.profit_factor)}</TableCell>
                <TableCell className="num text-right">
                  <div className="text-profit">{signed(r.best, 4)}</div>
                  <div className="text-loss">{signed(r.worst, 4)}</div>
                </TableCell>
                <TableCell className="num text-right text-muted-foreground">{hours(r.avg_hold_hours)}</TableCell>
                <TableCell className="num text-right text-muted-foreground">{usd(r.fees, 4)}</TableCell>
                <TableCell className="num text-[11.5px] text-muted-foreground">{x.last ? ago(x.last) : "—"}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </>
  );
}
