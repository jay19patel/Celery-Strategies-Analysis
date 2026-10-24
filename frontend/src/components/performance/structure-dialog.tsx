"use client";

import { Pnl, Premium } from "@/components/shared/money";
import { KV } from "@/components/shared/stat";
import { LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { dateTime, price, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Structure } from "@/lib/types";

const ORDER_TONE = { filled: "profit", failed: "loss", unknown: "warn", pending: "default" } as const;

/** One structure in full: its legs with entry and exit, and every order sent for it. */
export function StructureDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data: st, error, isLoading } = useApi<Structure>(id ? `/trading/structures/${id}` : null);
  return (
    <Dialog open={!!id} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{st ? `${st.label} ${st.underlying} · ${st.expiry_label}` : "Structure"}</DialogTitle>
          <DialogDescription className="num">{id}</DialogDescription>
        </DialogHeader>
        <LoadError error={error} />
        {isLoading && !st && <Loading rows={4} />}
        {st && (
          <div className="min-w-0 space-y-4">
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="primary">{st.broker}</Badge>
              <Badge>{st.source}</Badge>
              <Badge variant={st.status === "closed" ? "default" : st.status === "error" ? "loss" : "warn"}>{st.status}</Badge>
              <Badge>×{st.qty}</Badge>
            </div>
            <div className="grid gap-x-6 sm:grid-cols-2">
              <KV label="Entry" value={<Premium value={st.entry_cash} />} />
              <KV label="Realised P&L" value={<Pnl value={st.pnl} />} />
              <KV label="Opened" value={dateTime(st.opened_at)} />
              <KV label="Closed" value={dateTime(st.closed_at)} />
              <KV label="Spot in / out" value={`${price(st.entry_spot)} / ${price(st.exit_spot)}`} />
              <KV label="Fees" value={usd(st.fees, 4)} />
            </div>
            {st.close_reason && <p className="text-[13px] text-muted-foreground">{st.close_reason}</p>}
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Side</TableHead>
                    <TableHead>Contract</TableHead>
                    <TableHead className="text-right">Size</TableHead>
                    <TableHead className="text-right">Entry</TableHead>
                    <TableHead className="text-right">Exit</TableHead>
                    <TableHead className="text-right">Fees</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {st.legs.map((l) => (
                    <TableRow key={l.id}>
                      <TableCell className={l.side === "buy" ? "font-semibold text-profit" : "font-semibold text-loss"}>{l.side}</TableCell>
                      <TableCell className="num">{l.symbol}</TableCell>
                      <TableCell className="num text-right">{l.size}</TableCell>
                      <TableCell className="num text-right">{price(l.entry_price)}</TableCell>
                      <TableCell className="num text-right">{price(l.exit_price)}</TableCell>
                      <TableCell className="num text-right">{usd(l.entry_fee + l.exit_fee, 4)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            {!!st.orders?.length && (
              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Time</TableHead>
                      <TableHead>Purpose</TableHead>
                      <TableHead>Symbol</TableHead>
                      <TableHead>Side</TableHead>
                      <TableHead className="text-right">Fill</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {st.orders.map((o) => (
                      <TableRow key={o.client_order_id}>
                        <TableCell className="num text-xs">{dateTime(o.created_at)}</TableCell>
                        <TableCell>{o.purpose}</TableCell>
                        <TableCell className="num">{o.symbol}</TableCell>
                        <TableCell className={o.side === "buy" ? "text-profit" : "text-loss"}>{o.side}</TableCell>
                        <TableCell className="num text-right">{price(o.fill_price)}</TableCell>
                        <TableCell>
                          <Badge variant={ORDER_TONE[o.status]} title={o.error || undefined}>{o.status}</Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
