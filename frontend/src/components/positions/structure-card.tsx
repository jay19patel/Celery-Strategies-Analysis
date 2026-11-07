"use client";

import { type ReactNode, useState } from "react";
import { Loader2, Pencil, X } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { openCosts } from "@/components/positions/derive";
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
import { arrowSigned, dateTime, hours, num, price, signed, usd } from "@/lib/format";
import type { Leg, Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

function legPnl(leg: Leg): number | null {
  if (leg.entry_price === null || leg.mark === null) return null;
  return (leg.side === "buy" ? 1 : -1) * (leg.mark - leg.entry_price) * leg.size * leg.contract_value;
}

/** A charge, shown as money going out; one too small to show at four digits is not given a sign. */
function cost(v: number | null): ReactNode {
  if (v === null) return "—";
  if (v < 0.00005) return <span className="num">{usd(0, 4)}</span>;
  return <span className="num text-warn">−{usd(v, 4)}</span>;
}

function Tile({ label, hint, className, children }: { label: string; hint: string; className?: string; children: ReactNode }) {
  return (
    <div className={cn("min-w-0 rounded-xl border px-3 py-2", className)}>
      <div className="truncate text-[11.5px] text-muted-foreground">{label}</div>
      <div className="mt-0.5 truncate text-[15px] font-semibold">{children}</div>
      <div className="truncate text-[10.5px] text-muted-foreground">{hint}</div>
    </div>
  );
}

/** A move from entry as a signed percentage: green when it is on the profit side. */
function Pct({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return null;
  return <span className={cn("num text-[12px] font-medium", v >= 0 ? "text-profit" : "text-loss")}>({v > 0 ? "+" : ""}{v.toFixed(2)}%)</span>;
}

/** How far the stop has trailed behind the best price, and what it has locked in. */
function TrailNote({ s }: { s: Structure }) {
  const t = s.trail!;
  if (!t.active) {
    return (
      <p className="text-[11.5px] text-muted-foreground">
        Trailing stop: not yet active · it starts once price is 1R in profit (R = entry to the strategy&apos;s stop
        {s.initial_stop_pct != null ? `, ${Math.abs(s.initial_stop_pct).toFixed(2)}%` : ""}). Best so far {price(t.best)} <Pct v={t.best_pct} />.
      </p>
    );
  }
  return (
    <Notice tone="info">
      <strong>Trailing stop active.</strong> Moved {price(Math.abs(t.moved))} ({t.moved_r}R) from {price(t.initial_stop)} <Pct v={s.initial_stop_pct} /> to{" "}
      {price(s.stop_price)} <Pct v={s.stop_pct} />. Best price {price(t.best)} <Pct v={t.best_pct} />
      {t.locked_in_pct !== null && t.locked_in_pct >= 0 ? <> · at least {t.locked_in_pct.toFixed(2)}% is locked in</> : null}.
    </Notice>
  );
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

export function ExitsDialog({ s, open, onOpenChange }: { s: Structure; open: boolean; onOpenChange: (o: boolean) => void }) {
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

/** Close every held leg now; says how it went. Throws when the backend refuses, so a dialog stays open. */
export async function closeStructure(s: Structure): Promise<void> {
  const r = await api<Structure>(`/trading/structures/${s.id}/close`, "POST").catch((e: Error) => {
    toast.error("Close refused", { description: e.message });
    throw e;
  });
  if (r.status === "closed") toast.success(`Closed ${arrowSigned(r.pnl)}`);
  else toast.error(`Close did not finish (${r.status})`, { description: r.error });
}

export function StructureCard({ s }: { s: Structure }) {
  const [editing, setEditing] = useState(false);
  const pnl = s.live?.pnl ?? null;
  const costs = openCosts(s);
  // A position on the perpetual exits at its strategy's prices, not at a share of a premium: it has
  // no expiry to run out and its take-profit/stop percentages are ignored by the guard.
  const perp = s.instrument === "perp";

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
        {/* What closing now would book, and what it is made of. */}
        <div className="grid gap-4 lg:grid-cols-[minmax(0,260px)_minmax(0,1fr)] lg:items-center">
          <div className="space-y-2.5">
            <div>
              <div className="text-[11.5px] text-muted-foreground">Unrealised P&amp;L if closed now</div>
              <Pnl value={pnl} digits={4} className="text-2xl" />
              {s.live?.missing.length ? <div className="text-[11px] text-warn">no quote: {s.live.missing.join(", ")}</div> : null}
            </div>
            <Progress pnl={pnl} stop={s.stop_loss_usd} target={s.take_profit_usd} />
            {s.greeks && (
              <div className="num flex flex-wrap gap-x-3 text-[11.5px] text-muted-foreground" title="Of the legs still held, in the underlying's units, from the exchange's greeks">
                <span>Δ {num(s.greeks.delta, 4)}</span>
                <span>Γ {num(s.greeks.gamma, 6)}</span>
                <span>Θ {num(s.greeks.theta, 4)}/day</span>
                <span>V {num(s.greeks.vega, 4)}</span>
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
            <Tile label="Gross move" hint="at the marks"><Pnl value={costs.gross} digits={4} /></Tile>
            <Tile label="Charges paid" hint="on entry">{cost(costs.paid)}</Tile>
            <Tile label="Exit charges" hint="to close now (est.)">{cost(costs.exit)}</Tile>
            <Tile label="Spread" hint="bid/ask vs marks">{cost(costs.spread)}</Tile>
            <Tile label="Net if closed" hint="after all of it" className="col-span-2 sm:col-span-1"><Pnl value={pnl} digits={4} /></Tile>
          </div>
        </div>

        <div className="divide-y divide-slate-100 rounded-lg border md:hidden">
          {s.legs.map((l) => (
            <div key={l.id} className="space-y-1 px-3 py-2 text-[12px]">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate">
                  <span className={cn("mr-1.5 text-[11px] font-semibold uppercase", l.side === "buy" ? "text-profit" : "text-loss")}>{l.side} {l.size}</span>
                  <span className="num">{l.symbol}</span>
                </span>
                <Pnl value={l.exit_price === null ? legPnl(l) : null} digits={4} className="shrink-0" />
              </div>
              <div className="num flex flex-wrap justify-between gap-x-3 text-[11px] text-muted-foreground">
                <span>entry {price(l.entry_price)}</span>
                <span>{price(l.bid)} / {price(l.ask)}</span>
                <span>mark {price(l.mark)}</span>
              </div>
              {l.exit_price !== null && <div className="text-[11px] text-muted-foreground">exited {price(l.exit_price)}</div>}
            </div>
          ))}
        </div>
        <div className="hidden rounded-lg border md:block">
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
        </div>

        <div className="grid grid-cols-1 gap-x-8 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          <KV label="Entry" value={<Premium value={s.entry_cash} />} />
          {perp && <KV label="Entry spot" value={price(s.entry_spot)} />}
          <KV label="Max profit" value={s.max_profit === null ? "unlimited" : usd(s.max_profit, 4)} />
          <KV label="Max loss" value={s.max_loss === null ? <span className="text-loss">unlimited</span> : usd(s.max_loss, 4)} />
          {perp && (
            <KV
              label={s.trail?.active ? "Stop (trailed)" : "Stop"}
              value={<span className={(s.stop_pct ?? -1) >= 0 ? "text-profit" : "text-loss"}>{price(s.stop_price)} <Pct v={s.stop_pct} /></span>}
            />
          )}
          {perp && <KV label="Target" value={<span className="text-profit">{price(s.target_price)} <Pct v={s.target_pct} /></span>} />}
          {!perp && !s.held_to_settlement && s.take_profit_usd !== null && (
            <KV
              label={`Take profit · ${s.take_profit_pct}% of ${s.kind === "credit" ? "max profit" : "debit"}`}
              value={<span className="text-profit">{arrowSigned(s.take_profit_usd, 4)}{s.exit_prices && <span className="text-muted-foreground"> · option at {price(s.exit_prices.target)}</span>}</span>}
            />
          )}
          {!perp && !s.held_to_settlement && s.stop_loss_usd !== null && (
            <KV
              label={`Stop loss · ${s.stop_loss_pct}% of ${s.kind === "credit" ? "credit" : "debit"}`}
              value={<span className="text-loss">{arrowSigned(s.stop_loss_usd, 4)}{s.exit_prices && <span className="text-muted-foreground"> · option at {price(s.exit_prices.stop)}</span>}</span>}
            />
          )}
          <KV label={perp ? "Closes after" : "Expires in"} value={hours(s.hours_left)} />
          <KV label="Margin held" value={usd(s.margin, 2)} />
          <KV label="Opened" value={dateTime(s.opened_at)} />
        </div>
        {perp && s.trail && <TrailNote s={s} />}
        {s.held_to_settlement && (
          <Notice>
            Held to settlement: a credit capped by its wings has no stop, take profit or time exit. The most it can lose is{" "}
            {usd(s.max_loss, 4)}; it can be closed by hand at any time.
          </Notice>
        )}
        {s.reason && <p className="text-[11.5px] leading-relaxed text-muted-foreground">{s.reason}</p>}

        <div className="flex flex-wrap items-center justify-end gap-2 border-t pt-3">
          <span className="w-full text-[11.5px] text-muted-foreground sm:mr-auto sm:w-auto">
            {perp
              ? `${s.strategy} · exits at its own stop and target, the stop trailing from +1R`
              : s.held_to_settlement
                ? "held to settlement"
                : `take profit ${s.take_profit_pct}% of ${s.kind === "credit" ? "max profit" : "the debit"} · stop ${s.stop_loss_pct}%`}
          </span>
          {/* A perpetual's exits are prices its strategy set; the guard ignores these percentages. */}
          {!perp && !s.held_to_settlement && (
            <Button variant="outline" size="sm" onClick={() => setEditing(true)} className="h-10 flex-1 sm:h-8 sm:flex-none">
              <Pencil /> Exits
            </Button>
          )}
          <Confirm
            title={`Close ${s.label}?`}
            description={`Every leg is closed at the live book on ${s.broker} — shorts bought back first. Expected ${arrowSigned(pnl)}.`}
            confirmLabel="Close structure"
            destructive
            onConfirm={() => closeStructure(s)}
            trigger={(open) => (
              <Button variant="destructive-outline" size="sm" onClick={open} className="h-10 flex-1 sm:h-8 sm:flex-none">
                <X /> Close
              </Button>
            )}
          />
        </div>
      </CardContent>
      <ExitsDialog s={s} open={editing} onOpenChange={setEditing} />
    </Card>
  );
}
