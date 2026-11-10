"use client";

import { useMemo, useState } from "react";
import { ChevronRight, History } from "lucide-react";

import { duration, grossPnl, holdSeconds, reasonKind, returnPct, tally } from "@/components/performance/derive";
import { Pnl, Premium } from "@/components/shared/money";
import { Empty, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { arrowSigned, dateTime, num, pct, pnlClass, titleCase, usd } from "@/lib/format";
import type { Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

type Outcome = "" | "win" | "loss";
const ALL = "__all__";

/** A closed trade on a phone: the row's numbers stacked; a tap opens its detail. */
function TradeCard({ s, onOpen }: { s: Structure; onOpen: () => void }) {
  const ret = returnPct(s);
  return (
    <div>
      <button type="button" onClick={onOpen} className="block w-full px-4 py-3 text-left active:bg-slate-50">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[13.5px] font-semibold leading-snug text-slate-900">{s.label}</div>
            <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11.5px] text-muted-foreground">
              <span className="font-medium text-slate-700">{s.underlying}</span> {s.expiry_label}
              <Badge variant="primary">{s.broker}</Badge>
              <Badge variant={s.source === "auto" ? "primary" : "outline"}>{s.source}</Badge>
            </div>
          </div>
          <div className="shrink-0 text-right">
            <Pnl value={s.pnl} digits={4} className="text-[15px]" />
            <div className={cn("num text-[11px]", pnlClass(s.pnl))}>{ret === null ? "—" : `${num(ret, 2)}%`}</div>
          </div>
        </div>
        <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
          <div className="min-w-0"><div className="text-muted-foreground">Entry</div><Premium value={s.entry_cash} className="text-[11.5px]" /></div>
          <div className="min-w-0"><div className="text-muted-foreground">Gross</div><span className={cn("num font-medium", pnlClass(grossPnl(s)))}>{arrowSigned(grossPnl(s), 4)}</span></div>
          <div className="min-w-0"><div className="text-muted-foreground">Fees</div><span className="num">{usd(s.fees, 4)}</span></div>
        </div>
        <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
          <span className="num leading-snug">{dateTime(s.closed_at)} · {s.qty} lot{s.qty === 1 ? "" : "s"} · {duration(holdSeconds(s))}</span>
          <span className="flex shrink-0 items-center gap-1">
            <Badge title={s.close_reason}>{reasonKind(s.close_reason)}</Badge>
            <ChevronRight className="size-3.5 text-slate-400" />
          </span>
        </div>
      </button>
    </div>
  );
}

export function TradesTable({
  rows, loading, strategy, onStrategy, onOpen,
}: {
  rows: Structure[];
  loading: boolean;
  strategy: string;
  onStrategy: (s: string) => void;
  onOpen: (s: Structure) => void;
}) {
  const [outcome, setOutcome] = useState<Outcome>("");
  const [q, setQ] = useState("");
  const [per, setPer] = useState(10);
  const [page, setPage] = useState(1);

  const strategies = useMemo(() => [...new Set(rows.map((r) => r.strategy))].sort(), [rows]);
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows.filter(
      (r) =>
        (!strategy || r.strategy === strategy) &&
        (!outcome || (outcome === "win" ? (r.pnl ?? 0) > 0 : (r.pnl ?? 0) <= 0)) &&
        (!needle || [r.label, r.strategy, r.underlying, r.close_reason, r.id, ...r.legs.map((l) => l.symbol)].join(" ").toLowerCase().includes(needle)),
    );
  }, [rows, strategy, outcome, q]);
  const pages = Math.max(1, Math.ceil(filtered.length / per));
  const current = Math.min(page, pages);
  const shown = filtered.slice((current - 1) * per, current * per);
  const sum = tally(filtered);
  const filtering = !!(strategy || outcome || q);

  const go = (p: number) => setPage(Math.min(pages, Math.max(1, p)));
  const nums: number[] = [];
  for (let p = Math.max(1, current - 2); p <= Math.min(pages, current + 2); p++) nums.push(p);

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <History className="size-4 text-slate-400" /> Closed trades
          <span className="text-xs font-normal text-muted-foreground">{filtered.length} trade{filtered.length === 1 ? "" : "s"}{filtering ? " match" : ""}</span>
        </CardTitle>
        <div className="grid w-full grid-cols-[1fr_auto] gap-2 sm:flex sm:w-auto sm:flex-wrap sm:items-center">
          <Select value={strategy || ALL} onValueChange={(v) => { onStrategy(v === ALL ? "" : v); setPage(1); }}>
            <SelectTrigger size="sm" className="w-full text-[12.5px] sm:w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All strategies</SelectItem>
              {strategies.map((s) => (
                <SelectItem key={s} value={s}>{titleCase(s)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Tabs className="col-span-2 row-start-2" value={outcome || "all"} onValueChange={(v) => { setOutcome(v === "all" ? "" : (v as Outcome)); setPage(1); }}>
            <TabsList className="h-8 w-full sm:w-fit">
              <TabsTrigger value="all">All</TabsTrigger>
              <TabsTrigger value="win">Wins</TabsTrigger>
              <TabsTrigger value="loss">Losses</TabsTrigger>
            </TabsList>
          </Tabs>
          <Input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Symbol, reason, id…" className="col-span-2 h-9 w-full text-[12.5px] sm:h-8 sm:w-48" />
          <Select value={String(per)} onValueChange={(v) => { setPer(Number(v)); setPage(1); }}>
            <SelectTrigger size="sm" className="col-start-2 row-start-1 w-20 text-[12.5px]" aria-label="Trades per page">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[10, 25, 50, 100].map((n) => (
                <SelectItem key={n} value={String(n)}>{n}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </CardHeader>
      <div className="flex flex-wrap gap-x-5 gap-y-1 border-b border-slate-100 bg-slate-50/50 px-4 py-2 text-[12px]">
        <span>Net <b className={cn("num", pnlClass(sum.pnl))}>{arrowSigned(sum.pnl, 4)}</b></span>
        <span>Win rate <b className="num">{pct(sum.winRate)}</b> <span className="text-muted-foreground">({sum.wins} of {sum.trades})</span></span>
        <span>Fees <b className="num">{usd(sum.fees, 4)}</b></span>
        {filtering && (
          <button type="button" className="text-muted-foreground underline" onClick={() => { onStrategy(""); setOutcome(""); setQ(""); setPage(1); }}>
            clear filters
          </button>
        )}
      </div>
      {loading && !rows.length ? (
        <Loading className="p-4" />
      ) : !shown.length ? (
        <Empty title={`No closed trades${filtering ? " for these filters" : " yet"}`} />
      ) : (
        <>
        <div className="divide-y divide-slate-100 md:hidden">
          {shown.map((s) => (
            <TradeCard key={s.id} s={s} onOpen={() => onOpen(s)} />
          ))}
        </div>
        <Table className="hidden md:table">
          <TableHeader>
            <TableRow>
              <TableHead className="w-6" />
              <TableHead>Closed</TableHead>
              <TableHead>Strategy</TableHead>
              <TableHead>Structure</TableHead>
              <TableHead>Broker</TableHead>
              <TableHead className="text-right">Lots</TableHead>
              <TableHead className="text-right">Entry</TableHead>
              <TableHead className="text-right">Gross</TableHead>
              <TableHead className="text-right">Fees</TableHead>
              <TableHead className="text-right">Net P&L</TableHead>
              <TableHead className="text-right">Return</TableHead>
              <TableHead>Held</TableHead>
              <TableHead>Reason</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((s) => {
              const ret = returnPct(s);
              return (
                  <TableRow key={s.id} className="cursor-pointer" onClick={() => onOpen(s)}>
                    <TableCell className="pr-0">
                      <ChevronRight className="size-3.5 text-slate-400" />
                    </TableCell>
                    <TableCell className="num text-[11.5px] text-slate-500">{dateTime(s.closed_at)}</TableCell>
                    <TableCell className="min-w-44 max-w-64 whitespace-normal font-medium leading-snug">{s.label}</TableCell>
                    <TableCell>
                      <span className="font-medium">{s.underlying}</span> <span className="text-muted-foreground">{s.expiry_label}</span>
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Badge variant="primary">{s.broker}</Badge>
                        <Badge variant={s.source === "auto" ? "primary" : "outline"}>{s.source}</Badge>
                      </div>
                    </TableCell>
                    <TableCell className="num text-right">{s.qty}</TableCell>
                    <TableCell className="text-right"><Premium value={s.entry_cash} /></TableCell>
                    <TableCell className={cn("num text-right", pnlClass(grossPnl(s)))}>{arrowSigned(grossPnl(s), 4)}</TableCell>
                    <TableCell className="num text-right text-muted-foreground">{usd(s.fees, 4)}</TableCell>
                    <TableCell className="text-right"><Pnl value={s.pnl} digits={4} /></TableCell>
                    <TableCell className={cn("num text-right", pnlClass(s.pnl))}>{ret === null ? "—" : `${num(ret, 2)}%`}</TableCell>
                    <TableCell className="text-muted-foreground">{duration(holdSeconds(s))}</TableCell>
                    <TableCell><Badge title={s.close_reason}>{reasonKind(s.close_reason)}</Badge></TableCell>
                  </TableRow>
              );
            })}
          </TableBody>
        </Table>
        </>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-3 text-[12px]">
        <span className="num text-muted-foreground">
          {filtered.length ? `${(current - 1) * per + 1}–${Math.min(filtered.length, current * per)} of ${filtered.length}` : "0 of 0"}
        </span>
        <div className="flex items-center gap-1">
          <Button variant="outline" size="xs" disabled={current === 1} onClick={() => go(1)}>«</Button>
          <Button variant="outline" size="xs" disabled={current === 1} onClick={() => go(current - 1)}>‹</Button>
          {nums[0] > 1 && <span className="px-1 text-muted-foreground">…</span>}
          {nums.map((p) => (
            <Button key={p} variant={p === current ? "default" : "outline"} size="xs" onClick={() => go(p)} className={cn("num min-w-7", Math.abs(p - current) > 1 && "hidden sm:inline-flex")}>{p}</Button>
          ))}
          {nums[nums.length - 1] < pages && <span className="px-1 text-muted-foreground">…</span>}
          <Button variant="outline" size="xs" disabled={current === pages} onClick={() => go(current + 1)}>›</Button>
          <Button variant="outline" size="xs" disabled={current === pages} onClick={() => go(pages)}>»</Button>
        </div>
      </div>
    </Card>
  );
}
