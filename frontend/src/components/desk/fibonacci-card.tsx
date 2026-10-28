"use client";

// Fibonacci on the last impulse. The anchors are not a choice the page makes — the module picks
// the leg from the same swings the rest of the system reads, so two people looking at this at the
// same minute see the same levels. That is the only property that makes a level countable later.

import { ArrowDownRight, ArrowUpRight, Ruler } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty } from "@/components/shared/states";
import { pct, price } from "@/lib/format";
import type { Fib } from "@/lib/types";
import { cn } from "@/lib/utils";

export function FibonacciCard({ fib, currentPrice }: { fib: Fib | undefined; currentPrice: number | null }) {
  if (!fib?.ready || !fib.leg) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Ruler className="size-4" /> Fibonacci
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Empty title="No completed impulse">{fib?.note ?? "Waiting for stored bars."}</Empty>
        </CardContent>
      </Card>
    );
  }
  const up = fib.leg.direction === "up";
  const here = currentPrice ?? fib.price ?? 0;
  const retracements = fib.levels.filter((l) => l.kind === "retracement");
  const extensions = fib.levels.filter((l) => l.kind === "extension");

  const Level = ({ l }: { l: (typeof fib.levels)[number] }) => (
    <div
      className={cn(
        "flex items-baseline justify-between gap-3 rounded px-2 py-1 text-[12.5px]",
        l.golden && "bg-amber-50",
      )}
    >
      <span className="flex items-center gap-1.5 text-muted-foreground">
        {l.label}
        {l.golden && <Badge variant="warn">golden</Badge>}
      </span>
      <span className={cn("num font-medium", Math.abs(l.price - here) / (here || 1) < 0.002 && "text-primary")}>
        {price(l.price)}
      </span>
    </div>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Ruler className="size-4" /> Fibonacci
          <Badge variant={up ? "profit" : "loss"}>
            {up ? <ArrowUpRight className="size-3" /> : <ArrowDownRight className="size-3" />}
            {price(fib.leg.start_price)} → {price(fib.leg.end_price)}
          </Badge>
          {fib.in_golden && <Badge variant="warn">in the pocket</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-[11.5px] leading-relaxed text-muted-foreground">
          Price has retraced <span className="num font-medium text-foreground">{pct(fib.retraced, 1, true)}</span> of the last{" "}
          {up ? "up" : "down"} leg. Extensions are where that leg projects if the move continues — which is the number the
          Zero-Hero scan uses as its target, rather than assuming a move.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Retracements</div>
            {retracements.map((l) => (
              <Level key={l.label} l={l} />
            ))}
          </div>
          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Extensions (targets)</div>
            {extensions.map((l) => (
              <Level key={l.label} l={l} />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
