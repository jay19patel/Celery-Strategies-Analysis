"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { BrokerTabs, type BrokerFilter } from "@/components/shared/broker-tabs";
import { EquityCurve } from "@/components/performance/equity-curve";
import { StructureDialog } from "@/components/performance/structure-dialog";
import { SummaryTable } from "@/components/performance/summary-table";
import { Confirm } from "@/components/shared/confirm";
import { Pnl, Premium } from "@/components/shared/money";
import { PageHeader } from "@/components/shared/page";
import { Stat } from "@/components/shared/stat";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { dateTime, hours, num, pct, pnlClass, signed, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Stats, Structure } from "@/lib/types";

const PER_PAGE = 25;

export default function PerformancePage() {
  const [broker, setBroker] = useState<BrokerFilter>("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const stats = useApi<Stats>(`/portfolio/stats?broker=${broker}`);
  const closed = useApi<Structure[]>(`/trading/structures?status=closed&broker=${broker}&limit=1000`);

  const rows = closed.data ?? [];
  const pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
  const current = Math.min(page, pages - 1);
  const shown = rows.slice(current * PER_PAGE, (current + 1) * PER_PAGE);
  const o = stats.data?.overall;

  const reset = async () => {
    try {
      await api("/portfolio/paper/reset", "POST");
      toast.success("Paper account reset");
      await Promise.all([stats.mutate(), closed.mutate()]);
    } catch (e) {
      toast.error("Not reset", { description: e instanceof Error ? e.message : String(e) });
    }
  };

  return (
    <>
      <PageHeader
        title="Performance"
        description="Realised results of every closed structure."
        actions={
          <>
            <BrokerTabs value={broker} onChange={(v) => { setBroker(v); setPage(0); }} />
            <Confirm
              title="Reset the paper account?"
              description="Every closed paper structure and its orders are deleted and the paper balance starts again from the starting balance in Settings. Refused while a paper structure is open."
              confirmLabel="Reset"
              destructive
              onConfirm={reset}
              trigger={(open) => (
                <Button variant="outline" size="sm" onClick={open}>
                  <RotateCcw /> Reset paper
                </Button>
              )}
            />
          </>
        }
      />
      <LoadError error={stats.error} />

      {!o ? (
        <Loading rows={2} />
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <Stat label="Total P&L" value={signed(o.pnl)} valueClass={pnlClass(o.pnl)} hint={`${o.trades} trades`} />
          <Stat label="Win rate" value={pct(o.win_rate)} hint={`${o.wins} won · ${o.losses} lost`} />
          <Stat label="Trades" value={o.trades} />
          <Stat label="Profit factor" value={num(o.profit_factor)} hint="gross win / gross loss" />
          <Stat label="Max drawdown" value={usd(o.max_drawdown)} valueClass={o.max_drawdown ? "text-loss" : undefined} />
          <Stat label="Expectancy" value={signed(o.expectancy, 4)} valueClass={pnlClass(o.expectancy)} hint="per trade" />
          <Stat label="Avg hold" value={hours(o.avg_hold_hours)} />
          <Stat label="Fees" value={usd(o.fees, 4)} />
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Cumulative P&L</CardTitle>
        </CardHeader>
        <CardContent>
          {stats.data?.curve.length ? <EquityCurve curve={stats.data.curve} /> : <Empty title="No closed structures yet" />}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>By strategy</CardTitle>
          </CardHeader>
          {stats.data?.by_strategy.length ? <SummaryTable rows={stats.data.by_strategy} keyName="strategy" label="Strategy" /> : <Empty title="Nothing yet" />}
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Auto vs manual</CardTitle>
          </CardHeader>
          {stats.data?.by_source.length ? <SummaryTable rows={stats.data.by_source} keyName="source" label="Source" /> : <Empty title="Nothing yet" />}
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Closed structures</CardTitle>
          {rows.length > PER_PAGE && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Button variant="outline" size="icon-sm" disabled={current === 0} onClick={() => setPage(current - 1)} aria-label="Previous page">
                <ChevronLeft />
              </Button>
              <span className="num">{current + 1} / {pages}</span>
              <Button variant="outline" size="icon-sm" disabled={current >= pages - 1} onClick={() => setPage(current + 1)} aria-label="Next page">
                <ChevronRight />
              </Button>
            </div>
          )}
        </CardHeader>
        <LoadError error={closed.error} className="m-4" />
        {closed.isLoading && !closed.data ? (
          <Loading className="p-4" />
        ) : !rows.length ? (
          <Empty title="No closed structures" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Opened</TableHead>
                <TableHead>Closed</TableHead>
                <TableHead>Broker</TableHead>
                <TableHead>Structure</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Entry</TableHead>
                <TableHead className="text-right">P&L</TableHead>
                <TableHead>Reason</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((s) => (
                <TableRow key={s.id} className="cursor-pointer" onClick={() => setSelected(s.id)}>
                  <TableCell className="num text-xs">{dateTime(s.opened_at)}</TableCell>
                  <TableCell className="num text-xs">{dateTime(s.closed_at)}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Badge variant="primary">{s.broker}</Badge>
                      <Badge>{s.source}</Badge>
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className="font-medium">{s.label}</span> <span className="text-muted-foreground">{s.underlying} · {s.expiry_label}</span>
                  </TableCell>
                  <TableCell className="num text-right">{s.qty}</TableCell>
                  <TableCell className="text-right"><Premium value={s.entry_cash} /></TableCell>
                  <TableCell className="text-right"><Pnl value={s.pnl} /></TableCell>
                  <TableCell className="max-w-72 truncate text-muted-foreground" title={s.close_reason}>{s.close_reason}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      <StructureDialog id={selected} onClose={() => setSelected(null)} />
    </>
  );
}
