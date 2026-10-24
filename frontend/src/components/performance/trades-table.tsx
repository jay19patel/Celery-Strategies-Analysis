"use client";

import { Fragment, useMemo, useState, type ReactNode } from "react";
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
import { dateTime, num, pct, pnlClass, price, signed, titleCase, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

type Outcome = "" | "win" | "loss";
const ORDER_TONE = { filled: "profit", failed: "loss", unknown: "warn", pending: "default" } as const;
const ALL = "__all__";

function Field({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div>
      <div className="text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className={cn("num text-[12px]", className)}>{children}</div>
    </div>
  );
}

/** The expanded row: every number of the trade, then its legs and every order sent for it. */
function Detail({ s }: { s: Structure }) {
  const { data } = useApi<Structure>(`/trading/structures/${s.id}`);
  const legs = data?.legs ?? s.legs;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Field label="Opened">{dateTime(s.opened_at)}</Field>
        <Field label="Closed">{dateTime(s.closed_at)}</Field>
        <Field label="Held">{duration(holdSeconds(s))}</Field>
        <Field label="Premium in → out">{`${signed(s.entry_cash, 4)} → ${signed(s.exit_cash, 4)}`}</Field>
        <Field label="Max loss (margin)">{s.max_loss === null ? "unlimited" : usd(s.max_loss, 4)} ({usd(s.margin, 4)})</Field>
        <Field label="Spot in → out">{`${price(s.entry_spot)} → ${price(s.exit_spot)}`}</Field>
        <Field label="Exit reason">{reasonKind(s.close_reason)}</Field>
        <Field label="Gross P&L" className={pnlClass(grossPnl(s))}>{signed(grossPnl(s), 4)}</Field>
        <Field label="Fees">{usd(s.fees, 4)}</Field>
        <Field label="Net P&L" className={pnlClass(s.pnl)}>{signed(s.pnl, 4)}</Field>
        <Field label="Return on margin" className={pnlClass(s.pnl)}>{returnPct(s) === null ? "—" : pct(returnPct(s), 2)}</Field>
        <Field label="Take profit · stop">{`${s.take_profit_pct}% · ${s.stop_loss_pct}%`}</Field>
        <Field label="Source">{s.source}{s.decision_id ? ` · decision #${s.decision_id}` : ""}</Field>
      </div>
      <p className="text-[12px] text-slate-600">{s.close_reason}</p>
      <div>
        <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Legs</div>
        <table className="w-full max-w-3xl text-[11.5px]">
          <tbody>
            {legs.map((l) => (
              <tr key={l.id} className="border-t border-slate-100">
                <td className={cn("py-1 pr-3 font-semibold uppercase", l.side === "buy" ? "text-profit" : "text-loss")}>{l.side} {l.size}</td>
                <td className="num pr-3">{l.symbol}</td>
                <td className="num pr-3 text-right">{price(l.entry_price)} → {price(l.exit_price)}</td>
                <td className="num text-right text-muted-foreground">fees {usd(l.entry_fee + l.exit_fee, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!!data?.orders?.length && (
        <div>
          <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Leg orders</div>
          <table className="w-full max-w-3xl text-[11.5px]">
            <tbody>
              {data.orders.map((o) => (
                <tr key={o.client_order_id} className="border-t border-slate-100">
                  <td className="num py-1 pr-3 text-muted-foreground">{dateTime(o.created_at)}</td>
                  <td className="pr-3">{o.purpose}</td>
                  <td className={cn("pr-3 font-semibold uppercase", o.side === "buy" ? "text-profit" : "text-loss")}>{o.side} {o.filled_size || o.size}</td>
                  <td className="num pr-3">{o.symbol}</td>
                  <td className="num pr-3 text-right">@ {price(o.fill_price)}</td>
                  <td className="pr-3"><Badge variant={ORDER_TONE[o.status]} title={o.error || undefined}>{o.status}</Badge></td>
                  <td className="num text-muted-foreground">{o.client_order_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="num text-[11px] text-muted-foreground">structure {s.id}</div>
    </div>
  );
}

export function TradesTable({ rows, loading, strategy, onStrategy }: { rows: Structure[]; loading: boolean; strategy: string; onStrategy: (s: string) => void }) {
  const [outcome, setOutcome] = useState<Outcome>("");
  const [q, setQ] = useState("");
  const [per, setPer] = useState(10);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<Set<string>>(new Set());

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

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
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
        <div className="flex flex-wrap items-center gap-2">
          <Select value={strategy || ALL} onValueChange={(v) => { onStrategy(v === ALL ? "" : v); setPage(1); }}>
            <SelectTrigger size="sm" className="w-44 text-[12.5px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All strategies</SelectItem>
              {strategies.map((s) => (
                <SelectItem key={s} value={s}>{titleCase(s)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Tabs value={outcome || "all"} onValueChange={(v) => { setOutcome(v === "all" ? "" : (v as Outcome)); setPage(1); }}>
            <TabsList className="h-8">
              <TabsTrigger value="all">All</TabsTrigger>
              <TabsTrigger value="win">Wins</TabsTrigger>
              <TabsTrigger value="loss">Losses</TabsTrigger>
            </TabsList>
          </Tabs>
          <Input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Symbol, reason, id…" className="h-8 w-48 text-[12.5px]" />
          <Select value={String(per)} onValueChange={(v) => { setPer(Number(v)); setPage(1); }}>
            <SelectTrigger size="sm" className="w-20 text-[12.5px]" aria-label="Trades per page">
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
      <div className="flex flex-wrap gap-x-5 gap-y-1 border-b border-slate-100 px-4 py-2 text-[12px]">
        <span>Net <b className={cn("num", pnlClass(sum.pnl))}>{signed(sum.pnl, 4)}</b></span>
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
        <Table>
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
              const expanded = open.has(s.id);
              const ret = returnPct(s);
              return (
                <Fragment key={s.id}>
                  <TableRow className="cursor-pointer" onClick={() => toggle(s.id)}>
                    <TableCell className="pr-0">
                      <ChevronRight className={cn("size-3.5 text-slate-400 transition-transform", expanded && "rotate-90")} />
                    </TableCell>
                    <TableCell className="num text-[11.5px] text-slate-500">{dateTime(s.closed_at)}</TableCell>
                    <TableCell className="font-medium">{titleCase(s.strategy)}</TableCell>
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
                    <TableCell className={cn("num text-right", pnlClass(grossPnl(s)))}>{signed(grossPnl(s), 4)}</TableCell>
                    <TableCell className="num text-right text-muted-foreground">{usd(s.fees, 4)}</TableCell>
                    <TableCell className="text-right"><Pnl value={s.pnl} digits={4} /></TableCell>
                    <TableCell className={cn("num text-right", pnlClass(s.pnl))}>{ret === null ? "—" : `${num(ret, 2)}%`}</TableCell>
                    <TableCell className="text-muted-foreground">{duration(holdSeconds(s))}</TableCell>
                    <TableCell><Badge title={s.close_reason}>{reasonKind(s.close_reason)}</Badge></TableCell>
                  </TableRow>
                  {expanded && (
                    <TableRow className="bg-slate-50/70 hover:bg-slate-50/70">
                      <TableCell />
                      <TableCell colSpan={12} className="whitespace-normal py-3">
                        <Detail s={s} />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
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
            <Button key={p} variant={p === current ? "default" : "outline"} size="xs" onClick={() => go(p)} className="num min-w-7">{p}</Button>
          ))}
          {nums[nums.length - 1] < pages && <span className="px-1 text-muted-foreground">…</span>}
          <Button variant="outline" size="xs" disabled={current === pages} onClick={() => go(current + 1)}>›</Button>
          <Button variant="outline" size="xs" disabled={current === pages} onClick={() => go(pages)}>»</Button>
        </div>
      </div>
    </Card>
  );
}
