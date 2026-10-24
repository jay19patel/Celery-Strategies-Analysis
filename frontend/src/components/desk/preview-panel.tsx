"use client";

import { CheckCircle2, CircleAlert, XCircle } from "lucide-react";

import { PayoffChart } from "@/components/charts/payoff-chart";
import { Hint } from "@/components/shared/hint";
import { Pnl, Premium } from "@/components/shared/money";
import { KV } from "@/components/shared/stat";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { num, pct, price, signed, usd } from "@/lib/format";
import type { Preview } from "@/lib/types";
import { cn } from "@/lib/utils";

export function PreviewPanel({ preview }: { preview: Preview }) {
  const a = preview.analysis;
  return (
    <div className="space-y-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Side</TableHead>
            <TableHead>Contract</TableHead>
            <TableHead className="text-right">Size</TableHead>
            <TableHead className="text-right">Fill est.</TableHead>
            <TableHead className="text-right">Bid / Ask</TableHead>
            <TableHead className="text-right">Δ</TableHead>
            <TableHead className="text-right">IV</TableHead>
            <TableHead className="text-right">Spread</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {preview.plan.legs.map((l) => (
            <TableRow key={l.symbol}>
              <TableCell>
                <span className={cn("text-[11px] font-semibold uppercase", l.side === "buy" ? "text-profit" : "text-loss")}>{l.side}</span>
              </TableCell>
              <TableCell className="num">{l.symbol}</TableCell>
              <TableCell className="num text-right">{l.ratio * preview.qty}</TableCell>
              <TableCell className="num text-right font-medium">{price(l.price)}</TableCell>
              <TableCell className="num text-right text-muted-foreground">
                {price(l.bid)} / {price(l.ask)}
              </TableCell>
              <TableCell className="num text-right">{num(l.delta, 2)}</TableCell>
              <TableCell className="num text-right">{pct(l.iv, 1, true)}</TableCell>
              <TableCell className={cn("num text-right", (l.spread_pct ?? 0) > 30 ? "text-warn" : "text-muted-foreground")}>{pct(l.spread_pct, 0)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <div className="grid gap-x-8 gap-y-0 sm:grid-cols-2">
        <div>
          <KV label="Premium" value={<Premium value={a.net_premium} />} />
          <KV label="Max profit" value={a.max_profit === null ? <span className="text-profit">unlimited</span> : <Pnl value={a.max_profit} digits={4} />} />
          <KV label="Max loss" value={a.max_loss === null ? <span className="font-semibold text-loss">unlimited</span> : <Pnl value={-a.max_loss} digits={4} />} />
          <KV label="Breakevens" value={a.breakevens.map(price).join(" · ") || "—"} />
          <KV label="Reward : risk" value={a.reward_risk ? `1 : ${(1 / a.reward_risk).toFixed(1)}` : "—"} />
          <KV label="Margin set aside" value={usd(a.margin, 4)} />
        </div>
        <div>
          <KV label={<span className="flex items-center gap-1">POP at IV <Hint>Chance the structure is in profit at expiry if the settlement price follows the lognormal the ATM IV prices. Ignores an exit before expiry.</Hint></span>} value={pct(a.pop)} />
          <KV label={<span className="flex items-center gap-1">POP at realised vol <Hint>The same, using the volatility the underlying has actually realised. The gap to the line above is the volatility premium.</Hint></span>} value={pct(a.pop_realised)} />
          <KV label="Edge at realised vol" value={<Pnl value={a.expected_value_realised === null ? null : a.expected_value_realised - a.round_trip_fees} digits={4} />} />
          <KV label="Fees in / round trip" value={`${usd(a.entry_fees, 4)} / ${usd(a.round_trip_fees, 4)}`} />
          <KV label="Θ per day · Vega" value={`${signed(a.greeks.theta, 4)} · ${signed(a.greeks.vega, 4)}`} />
          <KV label="Δ · Γ" value={`${num(a.greeks.delta, 5)} · ${num(a.greeks.gamma, 7)}`} />
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {preview.checks.map((c) => (
          <div
            key={c.name}
            title={c.detail}
            className={cn(
              "flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11.5px]",
              c.ok ? "border-green-200 bg-profit-soft text-profit" : c.required ? "border-red-200 bg-loss-soft text-loss" : "border-amber-200 bg-warn-soft text-warn",
            )}
          >
            {c.ok ? <CheckCircle2 className="size-3.5" /> : c.required ? <XCircle className="size-3.5" /> : <CircleAlert className="size-3.5" />}
            <span className="font-medium">{c.name}</span>
            <span className="hidden text-[11px] opacity-80 md:inline">— {c.detail}</span>
          </div>
        ))}
      </div>

      {a.curve && <PayoffChart curve={a.curve} spot={preview.plan.spot} breakevens={a.breakevens} />}
    </div>
  );
}
