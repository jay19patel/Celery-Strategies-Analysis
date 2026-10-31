"use client";

import { useState } from "react";

import { BrokerTabs, type BrokerFilter } from "@/components/shared/broker-tabs";
import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { dateTime, price, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { OrderRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_TONE = { filled: "profit", failed: "loss", unknown: "warn", pending: "default" } as const;
type StatusFilter = "all" | OrderRecord["status"];

export default function OrdersPage() {
  const [broker, setBroker] = useState<BrokerFilter>("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const { data, error, isLoading } = useApi<OrderRecord[]>(`/trading/orders?broker=${broker}&limit=500`, { refreshInterval: 10_000 });
  const rows = (data ?? []).filter((o) => status === "all" || o.status === status);

  return (
    <>
      <PageHeader
        title="Orders"
        description="Every leg order sent to a broker: entries, closes, unwinds and settlements."
        actions={
          <>
            <BrokerTabs value={broker} onChange={setBroker} />
            <Select value={status} onValueChange={(v) => setStatus(v as StatusFilter)}>
              <SelectTrigger size="sm" className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                <SelectItem value="filled">Filled</SelectItem>
                <SelectItem value="failed">Failed</SelectItem>
                <SelectItem value="unknown">Unknown</SelectItem>
                <SelectItem value="pending">Pending</SelectItem>
              </SelectContent>
            </Select>
          </>
        }
      />
      <LoadError error={error} />
      <Card>
        {isLoading && !data ? (
          <Loading className="p-4" rows={6} />
        ) : !rows.length ? (
          <Empty title="No orders">Orders appear here once a structure is opened, manually or by the auto-trader.</Empty>
        ) : (
          <>
          <div className="divide-y divide-slate-100 md:hidden">
            {rows.map((o) => (
              <div key={o.client_order_id} className="space-y-1 px-4 py-3 text-[12px]">
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate">
                    <span className={cn("mr-1.5 font-semibold uppercase", o.side === "buy" ? "text-profit" : "text-loss")}>{o.side}</span>
                    <span className="num font-medium">{o.symbol}</span>
                  </span>
                  <Badge variant={STATUS_TONE[o.status]}>{o.status}</Badge>
                </div>
                <div className="num grid grid-cols-4 gap-2 text-[11px]">
                  <div><div className="text-muted-foreground">Size</div>{o.filled_size && o.filled_size !== o.size ? `${o.filled_size}/${o.size}` : o.size}</div>
                  <div><div className="text-muted-foreground">Limit</div>{price(o.limit_price)}</div>
                  <div><div className="text-muted-foreground">Fill</div>{price(o.fill_price)}</div>
                  <div><div className="text-muted-foreground">Fee</div>{usd(o.fee, 4)}</div>
                </div>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
                  <Badge variant="primary">{o.broker}</Badge>
                  <span>{o.purpose}</span>
                  <span className="num">{dateTime(o.created_at)}</span>
                  <span className="num break-all">{o.structure_id}</span>
                </div>
                {o.error && <div className="break-words text-[11px] text-loss">{o.error}</div>}
              </div>
            ))}
          </div>
          <Table className="hidden md:table">
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Broker</TableHead>
                <TableHead>Structure</TableHead>
                <TableHead>Purpose</TableHead>
                <TableHead>Symbol</TableHead>
                <TableHead>Side</TableHead>
                <TableHead className="text-right">Size</TableHead>
                <TableHead className="text-right">Limit</TableHead>
                <TableHead className="text-right">Fill</TableHead>
                <TableHead className="text-right">Fee</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Error</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((o) => (
                <TableRow key={o.client_order_id}>
                  <TableCell className="num text-xs">{dateTime(o.created_at)}</TableCell>
                  <TableCell><Badge variant="primary">{o.broker}</Badge></TableCell>
                  <TableCell className="num text-xs text-muted-foreground" title={o.client_order_id}>{o.structure_id}</TableCell>
                  <TableCell>{o.purpose}</TableCell>
                  <TableCell className="num">{o.symbol}</TableCell>
                  <TableCell className={cn("font-semibold", o.side === "buy" ? "text-profit" : "text-loss")}>{o.side}</TableCell>
                  <TableCell className="num text-right">{o.filled_size && o.filled_size !== o.size ? `${o.filled_size}/${o.size}` : o.size}</TableCell>
                  <TableCell className="num text-right">{price(o.limit_price)}</TableCell>
                  <TableCell className="num text-right">{price(o.fill_price)}</TableCell>
                  <TableCell className="num text-right">{usd(o.fee, 4)}</TableCell>
                  <TableCell><Badge variant={STATUS_TONE[o.status]}>{o.status}</Badge></TableCell>
                  <TableCell className="max-w-64 truncate text-xs text-loss" title={o.error}>{o.error}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </>
        )}
      </Card>
    </>
  );
}
