"use client";

import { useState } from "react";
import { Loader2, Pencil, X } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { Confirm } from "@/components/shared/confirm";
import { Pnl, Premium } from "@/components/shared/money";
import { Notice } from "@/components/shared/states";
import { KV } from "@/components/shared/stat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { dateTime, hours, price, signed, usd } from "@/lib/format";
import type { Leg, Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

function legPnl(leg: Leg): number | null {
  if (leg.entry_price === null || leg.mark === null) return null;
  return (leg.side === "buy" ? 1 : -1) * (leg.mark - leg.entry_price) * leg.size * leg.contract_value;
}

/** Where the P&L sits between the stop and the target, as a share of that span. */
function Progress({ pnl, stop, target }: { pnl: number | null; stop: number | null; target: number | null }) {
  if (pnl === null || stop === null || target === null || target <= stop) return null;
  const at = Math.min(1, Math.max(0, (pnl - stop) / (target - stop)));
  const zero = Math.min(1, Math.max(0, -stop / (target - stop)));
  return (
    <div className="space-y-1">
      <div className="relative h-2 rounded-full bg-gradient-to-r from-red-200 via-slate-100 to-green-200">
        <div className="absolute inset-y-0 w-px bg-slate-400" style={{ left: `${zero * 100}%` }} />
        <div className="absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-slate-800 shadow" style={{ left: `${at * 100}%` }} />
      </div>
      <div className="num flex justify-between text-[10.5px] text-muted-foreground">
        <span>stop {signed(stop, 2)}</span>
        <span>target {signed(target, 2)}</span>
      </div>
    </div>
  );
}

function ExitsDialog({ s, open, onOpenChange }: { s: Structure; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { mutate } = useSWRConfig();
  const [tp, setTp] = useState(String(s.take_profit_pct));
  const [sl, setSl] = useState(String(s.stop_loss_pct));
  const [busy, setBusy] = useState(false);
  const base = s.kind === "credit" ? "max profit" : "debit paid";
  const save = async () => {
    setBusy(true);
    try {
      await api(`/trading/structures/${s.id}`, "PATCH", { take_profit_pct: Number(tp), stop_loss_pct: Number(sl) });
      await mutate((k) => typeof k === "string" && k.startsWith("/trading"));
      toast.success("Exits updated");
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Exits for {s.label}</DialogTitle>
          <DialogDescription>The guard closes the structure when what closing would book reaches either level.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="tp">Take profit % of {base}</Label>
            <Input id="tp" type="number" value={tp} onChange={(e) => setTp(e.target.value)} className="num" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="sl">Stop loss % of {s.kind === "credit" ? "credit" : "debit"}</Label>
            <Input id="sl" type="number" value={sl} onChange={(e) => setSl(e.target.value)} className="num" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={save} disabled={busy}>{busy && <Loader2 className="animate-spin" />} Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function StructureCard({ s }: { s: Structure }) {
  const [editing, setEditing] = useState(false);
  const pnl = s.live?.pnl ?? null;
  // A position on the perpetual exits at its strategy's prices, not at a share of a premium: it has
  // no expiry to run out and its take-profit/stop percentages are ignored by the guard.
  const perp = s.instrument === "perp";
  const close = async () => {
    const r = await api<Structure>(`/trading/structures/${s.id}/close`, "POST").catch((e: Error) => {
      toast.error("Close refused", { description: e.message });
      throw e;
    });
    if (r.status === "closed") toast.success(`Closed ${signed(r.pnl)}`);
    else toast.error(`Close did not finish (${r.status})`, { description: r.error });
  };

  return (
    <Card className={cn(s.status === "error" && "border-red-300")}>
      <CardHeader>
        <CardTitle className="flex-wrap">
          {s.label}{" "}
          <span className="font-normal text-muted-foreground">
            {s.underlying} · {perp ? "perpetual" : s.expiry_label} · ×{s.qty}
          </span>
        </CardTitle>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={s.kind === "long" || s.kind === "credit" ? "profit" : "loss"}>{s.kind}</Badge>
          <Badge variant={s.broker === "delta" ? "primary" : "default"}>{s.broker}</Badge>
          <Badge variant={s.source === "auto" ? "primary" : "outline"}>{s.source}</Badge>
          {s.status !== "open" && <Badge variant={s.status === "error" ? "loss" : "warn"}>{s.status}</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {s.error && <Notice tone="loss">{s.error}</Notice>}
        <div className="grid gap-5 md:grid-cols-[220px_minmax(0,1fr)]">
          <div className="space-y-3">
            <div>
              <div className="text-[11.5px] text-muted-foreground">P&amp;L if closed now</div>
              <Pnl value={pnl} className="text-2xl" />
              <div className="num text-[11.5px] text-muted-foreground">at marks {signed(s.live?.pnl_mid ?? null)}</div>
              {s.live?.missing.length ? <div className="text-[11px] text-warn">no quote: {s.live.missing.join(", ")}</div> : null}
            </div>
            <Progress pnl={pnl} stop={s.stop_loss_usd} target={s.take_profit_usd} />
            <div>
              <KV label="Entry" value={<Premium value={s.entry_cash} />} />
              {perp && <KV label="Entry spot" value={price(s.entry_spot)} />}
              <KV label="Max profit" value={s.max_profit === null ? "unlimited" : usd(s.max_profit, 4)} />
              <KV label="Max loss" value={s.max_loss === null ? <span className="text-loss">unlimited</span> : usd(s.max_loss, 4)} />
              {perp ? (
                <>
                  <KV label="Stop" value={<span className="text-loss">{price(s.stop_price)}</span>} />
                  <KV label="Target" value={<span className="text-profit">{price(s.target_price)}</span>} />
                  <KV label="Closes after" value={hours(s.hours_left)} />
                </>
              ) : (
                <KV label="Expires in" value={hours(s.hours_left)} />
              )}
              <KV label="Margin held" value={usd(s.margin, 2)} />
              <KV label="Fees so far" value={usd(s.fees, 4)} />
              <KV label="Opened" value={dateTime(s.opened_at)} />
            </div>
            {s.reason && <p className="text-[11.5px] leading-relaxed text-muted-foreground">{s.reason}</p>}
          </div>
          <div className="min-w-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Leg</TableHead>
                  <TableHead className="text-right">Size</TableHead>
                  <TableHead className="text-right">Entry</TableHead>
                  <TableHead className="text-right">Bid / Ask</TableHead>
                  <TableHead className="text-right">Mark</TableHead>
                  <TableHead className="text-right">P&amp;L (mark)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {s.legs.map((l) => (
                  <TableRow key={l.id}>
                    <TableCell>
                      <span className={cn("mr-2 text-[11px] font-semibold uppercase", l.side === "buy" ? "text-profit" : "text-loss")}>{l.side}</span>
                      <span className="num">{l.symbol}</span>
                      {l.exit_price !== null && <span className="ml-2 text-[11px] text-muted-foreground">exited {price(l.exit_price)}</span>}
                    </TableCell>
                    <TableCell className="num text-right">{l.size}</TableCell>
                    <TableCell className="num text-right">{price(l.entry_price)}</TableCell>
                    <TableCell className="num text-right text-muted-foreground">{price(l.bid)} / {price(l.ask)}</TableCell>
                    <TableCell className="num text-right">{price(l.mark)}</TableCell>
                    <TableCell className="text-right"><Pnl value={l.exit_price === null ? legPnl(l) : null} digits={4} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
              <span className="mr-auto text-[11.5px] text-muted-foreground">
                {perp
                  ? `${s.strategy} · exits at its own stop and target`
                  : `take profit ${s.take_profit_pct}% of ${s.kind === "credit" ? "max profit" : "the debit"} · stop ${s.stop_loss_pct}%`}
              </span>
              {/* A perpetual's exits are prices its strategy set; the guard ignores these percentages. */}
              {!perp && (
                <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                  <Pencil /> Exits
                </Button>
              )}
              <Confirm
                title={`Close ${s.label}?`}
                description={`Every leg is closed at the live book on ${s.broker} — shorts bought back first. Expected ${signed(pnl)}.`}
                confirmLabel="Close structure"
                destructive
                onConfirm={close}
                trigger={(open) => (
                  <Button variant="destructive-outline" size="sm" onClick={open}>
                    <X /> Close
                  </Button>
                )}
              />
            </div>
          </div>
        </div>
      </CardContent>
      <ExitsDialog s={s} open={editing} onOpenChange={setEditing} />
    </Card>
  );
}
