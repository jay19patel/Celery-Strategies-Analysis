"use client";

// The candidate table. Two columns here carry the whole proposition and sit next to each other on
// purpose: what the contract is worth if the move happens, and what the same contract is worth if
// nothing happens. A page that showed only the first would be a brochure.

import { useState } from "react";
import { ChevronDown } from "lucide-react";

import { CheckList } from "@/components/zerohero/checks";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { num, pct, price, signed, signedPct, usd } from "@/lib/format";
import type { ZhCandidate } from "@/lib/types";
import { cn } from "@/lib/utils";

function Row({ c, best }: { c: ZhCandidate; best: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <TableRow className={cn("cursor-pointer", best && "bg-accent/40")} onClick={() => setOpen((v) => !v)}>
        <TableCell className="font-medium">
          <div className="flex items-center gap-1.5">
            <ChevronDown className={cn("size-3.5 text-muted-foreground transition-transform", open && "rotate-180")} />
            {price(c.strike)}
            <Badge variant={c.kind === "call" ? "primary" : "solid"}>{c.kind}</Badge>
            {best && <Badge variant="primary">best EV</Badge>}
          </div>
        </TableCell>
        <TableCell className="num text-right">{signedPct(c.distance_pct)}</TableCell>
        <TableCell className="num text-right">{usd(c.paid, 2)}</TableCell>
        <TableCell className="num text-right font-semibold text-profit">{c.multiple === null ? "—" : `${num(c.multiple, 1)}x`}</TableCell>
        <TableCell className="num text-right text-loss">{c.decay_if_flat === null ? "—" : `${num(c.decay_if_flat, 2)}x`}</TableCell>
        <TableCell className="num text-right">{c.p_touch === null ? "—" : pct(c.p_touch, 0, true)}</TableCell>
        <TableCell className={cn("num text-right font-semibold", (c.ev ?? 0) > 0 ? "text-profit" : "text-loss")}>{signed(c.ev, 2)}</TableCell>
        <TableCell className="num text-right">{pct(c.spread_pct, 0)}</TableCell>
        <TableCell className="num text-right">{num(c.capacity_lots, 0)}</TableCell>
      </TableRow>
      {open && (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={9} className="bg-muted/40 px-4 py-3">
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <div className="mb-1 text-[11.5px] font-semibold uppercase tracking-wide text-muted-foreground">Contract checks</div>
                <CheckList checks={c.checks} />
              </div>
              <div className="space-y-1 text-[12.5px] text-muted-foreground">
                <div className="mb-1 text-[11.5px] font-semibold uppercase tracking-wide">The arithmetic</div>
                <p>
                  Worth <span className="num font-medium text-foreground">{usd(c.value_at_target, 2)}</span> if price moves{" "}
                  <span className="num font-medium text-foreground">{signedPct(c.target_pct)}</span> within{" "}
                  <span className="num font-medium text-foreground">{num(c.hold_minutes, 0)} min</span>, after giving half the spread back on the exit.
                </p>
                <p>
                  Realised volatility puts that move at <span className="num font-medium text-foreground">{pct(c.p_touch, 1, true)}</span>. Against a{" "}
                  stop at a share of the debit, that is <span className="num font-medium text-foreground">{signed(c.ev, 2)}</span> expected per unit paid.
                </p>
                <p className="text-[11.5px]">
                  IV {pct(c.iv, 1, true)} · Δ {num(c.delta, 3)} · Γ {num(c.gamma, 6)} · Θ {num(c.theta, 1)} · OI {num(c.oi, 1)} · vol {num(c.volume, 1)}
                </p>
              </div>
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

export function CandidateTable({ candidates }: { candidates: ZhCandidate[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Strike</TableHead>
            <TableHead className="text-right">OTM</TableHead>
            <TableHead className="text-right">Ask</TableHead>
            <TableHead className="text-right">At target</TableHead>
            <TableHead className="text-right">If flat</TableHead>
            <TableHead className="text-right">P(touch)</TableHead>
            <TableHead className="text-right">EV / unit</TableHead>
            <TableHead className="text-right">Spread</TableHead>
            <TableHead className="text-right">Capacity</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {candidates.map((c, i) => (
            <Row key={c.symbol} c={c} best={i === 0} />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
