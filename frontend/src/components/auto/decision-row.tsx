"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Pnl, Premium } from "@/components/shared/money";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { dateTime, pct, price, signedPct } from "@/lib/format";
import type { Decision } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACTION = { open: "profit", skip: "default", error: "loss" } as const;

export function DecisionRow({ d }: { d: Decision }) {
  const [open, setOpen] = useState(false);
  const m = d.regime?.metrics;
  return (
    <div className="border-b border-slate-100 last:border-0">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50/70 active:bg-slate-50">
        {open ? <ChevronDown className="mt-0.5 size-4 shrink-0 text-slate-400" /> : <ChevronRight className="mt-0.5 size-4 shrink-0 text-slate-400" />}
        <div className="min-w-0 flex-1 sm:flex sm:items-start sm:gap-3">
          <div className="flex items-center gap-2 sm:contents">
            <span className="num shrink-0 text-[12px] text-muted-foreground sm:w-28">{dateTime(d.ts)}</span>
            <Badge variant={ACTION[d.action]} className="w-12 shrink-0 uppercase">{d.action}</Badge>
            <span className="shrink-0 text-[12.5px] font-semibold sm:w-10">{d.underlying}</span>
          </div>
          <span className="mt-1 block min-w-0 text-[12.5px] text-slate-700 sm:mt-0 sm:flex-1">
            {d.strategy && <span className="mr-1.5 font-medium capitalize">{d.strategy.replace(/_/g, " ")} ·</span>}
            {d.reason}
          </span>
        </div>
      </button>
      {open && (
        <div className="animate-in fade-in duration-150 space-y-4 bg-slate-50/50 px-4 pb-4 sm:pl-11">
          {d.regime && m && (
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-[12px] text-muted-foreground">
              <span>regime <b className="capitalize text-slate-800">{d.regime.label}</b></span>
              <span>direction <b className="num text-slate-800">{d.regime.direction.toFixed(2)}</b></span>
              <span>spot <b className="num text-slate-800">{price(d.spot)}</b></span>
              <span>IV <b className="num text-slate-800">{pct(m.atm_iv, 1, true)}</b></span>
              <span>realised <b className="num text-slate-800">{pct(m.rv_24h, 1, true)}</b> / <b className="num text-slate-800">{pct(m.rv_7d, 1, true)}</b></span>
              <span>24h <b className="num text-slate-800">{signedPct(m.change_24h_pct)}</b></span>
              <span>to expiry <b className="num text-slate-800">{d.regime.hours_to_expiry}h</b></span>
              {d.regime.min_score != null && (
                <span>score to beat <b className="num text-slate-800">{d.regime.min_score}</b></span>
              )}
            </div>
          )}

          {d.candidates && d.candidates.length > 0 && (
            <div className="overflow-hidden rounded-xl bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Strategy</TableHead>
                    <TableHead className="text-right">Score</TableHead>
                    <TableHead className="text-right">Premium / lot</TableHead>
                    <TableHead className="text-right">Max loss</TableHead>
                    <TableHead className="text-right">POP</TableHead>
                    <TableHead className="text-right">Edge</TableHead>
                    <TableHead>Outcome</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {d.candidates.map((c) => (
                    <TableRow key={c.strategy} className={cn(c.strategy === d.strategy && d.action === "open" && "bg-profit-soft/60")}>
                      <TableCell className="font-medium" title={c.reasons.join("\n")}>{c.label}</TableCell>
                      <TableCell className="num text-right">{c.score}</TableCell>
                      <TableCell className="text-right">{c.analysis ? <Premium value={c.analysis.net_premium} /> : "—"}</TableCell>
                      <TableCell className="num text-right">{c.analysis ? (c.analysis.max_loss === null ? "unlimited" : c.analysis.max_loss.toFixed(4)) : "—"}</TableCell>
                      <TableCell className="num text-right">{pct(c.analysis?.pop)}</TableCell>
                      <TableCell className="text-right">
                        {c.analysis?.expected_value_realised != null ? <Pnl value={c.analysis.expected_value_realised - c.analysis.round_trip_fees} digits={4} /> : "—"}
                      </TableCell>
                      <TableCell className={cn("whitespace-normal text-[12px]", c.status === "candidate" ? "text-profit" : "text-muted-foreground")}>{c.status}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {d.orders && (
            <div className="flex flex-wrap gap-2 text-[12px]">
              {Object.entries(d.orders).map(([broker, o]) => (
                <div key={broker} className="min-w-0 break-words rounded-lg bg-card px-2.5 py-1.5">
                  <span className="font-semibold capitalize">{broker}</span>:{" "}
                  {o.refused ? <span className="text-muted-foreground">{o.refused}</span> : <span className={o.status === "open" ? "text-profit" : "text-loss"}>{o.status} ×{o.qty} <span className="num">{o.structure_id}</span>{o.error ? ` — ${o.error}` : ""}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
