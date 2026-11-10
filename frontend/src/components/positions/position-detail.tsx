"use client";

// One position, open or closed, the same way everywhere it can be opened from: what it is, why it
// opened (and closed), its numbers, every leg and every order sent for it.

import type { ReactNode } from "react";

import { duration, holdSeconds } from "@/components/performance/derive";
import { Pnl } from "@/components/shared/money";
import { LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { dateTime, hours, num, pct, price, signed, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_TONE = { open: "profit", opening: "warn", closing: "warn", error: "loss", closed: "default", failed: "loss" } as const;
const ORDER_TONE = { filled: "profit", failed: "loss", unknown: "warn", pending: "default" } as const;

function Fact({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-secondary/60 px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("num mt-0.5 text-[13px] font-medium leading-snug", className)}>{children}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

/** The facts that differ between a perpetual and an option structure, the rest shared. */
function facts(s: Structure): [string, ReactNode, string?][] {
  const open = s.status !== "closed" && s.status !== "failed";
  const pnl = open ? s.live?.pnl ?? null : s.pnl;
  const onMargin = pnl !== null && s.margin > 0 ? (100 * pnl) / s.margin : null;
  const common: [string, ReactNode, string?][] = [
    ["Opened", dateTime(s.opened_at)],
    [open ? "Time left" : "Closed", open ? (s.instrument === "perp" ? `held up to ${dateTime(s.expiry)}` : `${hours(s.hours_left)} to expiry`) : dateTime(s.closed_at)],
    ["Held", duration(open ? Date.now() / 1000 - s.opened_at : holdSeconds(s))],
    ["Size", `${s.qty} ${s.instrument === "perp" ? "contracts" : "lots"}`],
    ["Spot in → out", `${price(s.entry_spot)} → ${open ? price(s.live?.spot ?? null) + " now" : price(s.exit_spot)}`],
  ];
  const kind: [string, ReactNode, string?][] = s.instrument === "perp"
    ? [
        ["Stop", `${price(s.stop_price)}${s.stop_pct != null ? ` (${signed(s.stop_pct)}%)` : ""}${s.trail?.active ? " · trailed" : ""}`, "text-loss"],
        ["Target", `${price(s.target_price)}${s.target_pct != null ? ` (${signed(s.target_pct)}%)` : ""}`, "text-profit"],
      ]
    : [
        ["Premium", `${usd(Math.abs(s.entry_cash), 4)} ${s.entry_cash >= 0 ? "credit" : "debit"}`],
        ["Exits", s.held_to_settlement ? "held to settlement" : `take profit ${s.take_profit_pct}% · stop ${s.stop_loss_pct}%`],
      ];
  return [
    ...common,
    ...kind,
    ["Max profit", s.max_profit === null ? "unlimited" : usd(s.max_profit, 4), "text-profit"],
    ["Max loss", s.max_loss === null ? "unlimited" : usd(s.max_loss, 4), "text-loss"],
    ["Margin", usd(s.margin, 4)],
    ["Fees", open ? `${usd(s.live?.fees_paid ?? s.fees, 4)} paid${s.live?.exit_fees != null ? ` + ${usd(s.live.exit_fees, 4)} to close` : ""}` : usd(s.fees, 4)],
    ["Return on margin", onMargin === null ? "—" : `${signed(onMargin)}%`, onMargin === null ? undefined : onMargin >= 0 ? "text-profit" : "text-loss"],
  ];
}

export function PositionDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data: s, error } = useApi<Structure>(id ? `/trading/structures/${id}` : null, { refreshInterval: 10_000 });
  const open = s ? s.status !== "closed" && s.status !== "failed" : false;
  const pnl = s ? (open ? s.live?.pnl ?? null : s.pnl) : null;

  return (
    <Dialog open={!!id} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] gap-5 overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2 pr-6 text-[17px]">
            {s?.label ?? "Position"}
            {s && <span className="num text-[13px] font-normal text-muted-foreground">{s.underlying}</span>}
          </DialogTitle>
          <DialogDescription asChild>
            <div className="flex flex-wrap items-center gap-1.5">
              {s && (
                <>
                  <Badge variant={STATUS_TONE[s.status]}>{s.status}</Badge>
                  <Badge variant="outline" className="capitalize">{s.broker}</Badge>
                  <Badge variant="outline">{s.source}</Badge>
                  <Badge variant="outline">{s.kind}</Badge>
                  {s.strategy_version != null && <span className="text-[11px] text-muted-foreground">strategy v{s.strategy_version}</span>}
                </>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        <LoadError error={error} />
        {!s && !error && <Loading rows={5} />}
        {s && (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3 rounded-2xl bg-card">
              <div>
                <div className="text-[12px] text-muted-foreground">{open ? "P&L if closed now" : "Realised P&L, after fees"}</div>
                <Pnl value={pnl} digits={4} className="text-[26px]" />
              </div>
              {s.error && <p className="max-w-md text-[12.5px] text-loss">{s.error}</p>}
            </div>

            <Section title="Why">
              <div className="space-y-1.5 text-[13px] leading-relaxed">
                <p><span className="text-muted-foreground">Opened: </span>{s.reason || (s.source === "manual" ? "from the dashboard" : s.decision_id ? `option strategy cycle #${s.decision_id} (see Option Decisions)` : "—")}</p>
                {s.close_reason && <p><span className="text-muted-foreground">Closed: </span>{s.close_reason}</p>}
              </div>
            </Section>

            <Section title="Numbers">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {facts(s).map(([label, value, cls]) => (
                  <Fact key={label} label={label} className={cls}>{value}</Fact>
                ))}
              </div>
            </Section>

            <Section title={`Legs (${s.legs.length})`}>
              <div className="overflow-x-auto rounded-xl border">
                <table className="w-full text-[12.5px] [&_td]:whitespace-nowrap [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-medium [&_th]:text-muted-foreground">
                  <thead className="bg-secondary/60">
                    <tr><th>Side</th><th>Contract</th><th className="text-right">Entry</th><th className="text-right">{open ? "Bid / ask now" : "Exit"}</th><th className="text-right">Fees</th></tr>
                  </thead>
                  <tbody>
                    {s.legs.map((l) => (
                      <tr key={l.id} className="border-t">
                        <td className={cn("font-semibold uppercase", l.side === "buy" ? "text-profit" : "text-loss")}>{l.side} {l.size}</td>
                        <td className="num">{l.symbol}</td>
                        <td className="num text-right">{price(l.entry_price)}</td>
                        <td className="num text-right">{l.exit_price != null ? price(l.exit_price) : open ? `${price(l.bid)} / ${price(l.ask)}` : "—"}</td>
                        <td className="num text-right text-muted-foreground">{usd(l.entry_fee + l.exit_fee, 4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>

            <Section title={`Orders (${s.orders?.length ?? 0})`}>
              {s.orders?.length ? (
                <div className="overflow-x-auto rounded-xl border">
                  <table className="w-full text-[12.5px] [&_td]:whitespace-nowrap [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-medium [&_th]:text-muted-foreground">
                    <thead className="bg-secondary/60">
                      <tr><th>Time</th><th>For</th><th>Side</th><th>Contract</th><th className="text-right">Limit</th><th className="text-right">Filled at</th><th className="text-right">Fee</th><th>Status</th></tr>
                    </thead>
                    <tbody>
                      {s.orders.map((o) => (
                        <tr key={o.client_order_id} className="border-t" title={`${o.client_order_id}${o.exchange_id ? ` · exchange ${o.exchange_id}` : ""}`}>
                          <td className="num text-muted-foreground">{dateTime(o.created_at)}</td>
                          <td className="capitalize">{o.purpose}</td>
                          <td className={cn("font-semibold uppercase", o.side === "buy" ? "text-profit" : "text-loss")}>{o.side} {o.filled_size || o.size}</td>
                          <td className="num">{o.symbol}</td>
                          <td className="num text-right">{price(o.limit_price)}</td>
                          <td className="num text-right">{price(o.fill_price)}</td>
                          <td className="num text-right text-muted-foreground">{usd(o.fee, 4)}</td>
                          <td>
                            <Badge variant={ORDER_TONE[o.status] ?? "default"}>{o.status}</Badge>
                            {o.error && <span className="ml-2 whitespace-normal text-[11.5px] text-muted-foreground">{o.error}</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-[12.5px] text-muted-foreground">No order recorded.</p>
              )}
            </Section>

            {open && s.greeks && (
              <Section title="Greeks now">
                <div className="grid grid-cols-4 gap-2">
                  {(["delta", "gamma", "theta", "vega"] as const).map((g) => (
                    <Fact key={g} label={g}>{num(s.greeks?.[g] ?? null, 4)}</Fact>
                  ))}
                </div>
              </Section>
            )}

            <p className="num break-all text-[11px] text-muted-foreground">
              {s.id}{s.decision_id ? ` · decision #${s.decision_id}` : ""}{s.margin > 0 && s.pnl !== null && !open ? ` · ${pct((100 * s.pnl) / s.margin, 2)} on margin` : ""}
            </p>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
