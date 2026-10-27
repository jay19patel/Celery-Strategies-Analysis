"use client";

import { Layers } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { StrategySpec } from "@/lib/types";

/** Every structure the desk can build. Clicking one loads it into the builder above — it belongs
 *  next to the chain and the builder that use it, not on the directional Strategies page. */
export function StrategyCatalog({
  strategies,
  selected,
  onPick,
}: {
  strategies: StrategySpec[];
  selected: string;
  onPick: (key: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Layers className="size-4 text-slate-400" /> Options catalog
        </CardTitle>
        <span className="text-[11.5px] text-muted-foreground">click one to load it into the builder</span>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {strategies.map((spec) => (
          <button
            key={spec.key}
            type="button"
            onClick={() => onPick(spec.key)}
            aria-pressed={selected === spec.key}
            className={`rounded-lg border p-3 text-left transition-colors ${
              selected === spec.key ? "border-primary bg-primary/5" : "hover:bg-muted/40"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <Badge
                variant={
                  spec.view === "bullish" ? "profit" : spec.view === "bearish" ? "loss" : spec.view === "volatile" ? "solid" : "default"
                }
                className="text-[10px] capitalize"
              >
                {spec.view}
              </Badge>
              <div className="flex items-center gap-1.5">
                <Badge variant="outline" className="font-mono text-[10px] uppercase">{spec.premium}</Badge>
                <Badge variant="outline" className="font-mono text-[10px] capitalize">{spec.risk} risk</Badge>
              </div>
            </div>
            <div className="mt-2 text-[13px] font-semibold">{spec.label}</div>
            <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">{spec.summary}</p>
          </button>
        ))}
      </CardContent>
    </Card>
  );
}
