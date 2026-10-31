"use client";

import { Minus, Plus, Trash2 } from "lucide-react";

import type { PickedLeg } from "@/components/desk/chain-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { StrategySpec } from "@/lib/types";
import { cn } from "@/lib/utils";

const VIEW_TONE = { neutral: "default", bullish: "profit", bearish: "loss", volatile: "warn" } as const;

export interface TicketParams {
  strategy: string;
  qty: number;
  shortDelta: number;
  wingWidth: number;
  legs: PickedLeg[];
}

export function StrategyPicker({ strategies, params, onChange }: { strategies: StrategySpec[]; params: TicketParams; onChange: (p: Partial<TicketParams>) => void }) {
  const spec = strategies.find((s) => s.key === params.strategy);
  const usesDelta = ["iron_condor", "short_strangle", "bull_put_spread", "bear_call_spread"].includes(params.strategy);
  const usesWings = ["iron_condor", "iron_butterfly", "bull_put_spread", "bear_call_spread"].includes(params.strategy);

  return (
    <div className="space-y-4">
      <div className="flex gap-1.5 overflow-x-auto pb-1 no-scrollbar sm:flex-wrap sm:pb-0 -mx-1 px-1">
        {strategies.map((s) => (
          <button
            key={s.key}
            onClick={() => onChange({ strategy: s.key })}
            className={cn(
              "shrink-0 rounded-lg border bg-card px-2.5 sm:px-3 py-1.5 text-[12px] sm:text-[12.5px] font-medium transition-all active:scale-95 hover:border-slate-300 shadow-2xs",
              params.strategy === s.key && "border-primary bg-accent text-accent-foreground ring-1 ring-primary/20",
            )}
          >
            {s.label}
          </button>
        ))}
        <button
          onClick={() => onChange({ strategy: "custom" })}
          className={cn(
            "shrink-0 rounded-lg border border-dashed bg-card px-2.5 sm:px-3 py-1.5 text-[12px] sm:text-[12.5px] font-medium transition-all active:scale-95 hover:border-slate-400 shadow-2xs",
            params.strategy === "custom" && "border-primary bg-accent text-accent-foreground",
          )}
        >
          Custom legs
        </button>
      </div>

      {spec && (
        <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted-foreground">
          <Badge variant={VIEW_TONE[spec.view]} className="capitalize">{spec.view}</Badge>
          <Badge variant="outline">{spec.premium}</Badge>
          <Badge variant={spec.risk === "defined" ? "primary" : "warn"}>{spec.risk === "defined" ? "defined risk" : "unlimited risk"}</Badge>
          <span>{spec.summary}</span>
        </div>
      )}

      {params.strategy === "custom" && (
        <div className="rounded-lg border bg-slate-50/60 p-3">
          {params.legs.length === 0 ? (
            <p className="text-[12.5px] text-muted-foreground">Click a bid in the chain to sell that option, an ask to buy it. All legs must share one expiry.</p>
          ) : (
            <div className="space-y-1.5">
              {params.legs.map((leg, i) => (
                <div key={leg.symbol} className="flex items-center gap-2 text-[12.5px]">
                  <button
                    onClick={() => onChange({ legs: params.legs.map((l, j) => (j === i ? { ...l, side: l.side === "buy" ? "sell" : "buy" } : l)) })}
                    className={cn("w-12 rounded px-1.5 py-0.5 text-center text-[11px] font-semibold uppercase", leg.side === "buy" ? "bg-profit-soft text-profit" : "bg-loss-soft text-loss")}
                  >
                    {leg.side}
                  </button>
                  <span className="num flex-1 truncate">{leg.symbol}</span>
                  <Button variant="ghost" size="icon-sm" className="size-8 sm:size-6" onClick={() => onChange({ legs: params.legs.map((l, j) => (j === i ? { ...l, ratio: Math.max(1, l.ratio - 1) } : l)) })}>
                    <Minus className="size-3" />
                  </Button>
                  <span className="num w-5 text-center">{leg.ratio}</span>
                  <Button variant="ghost" size="icon-sm" className="size-8 sm:size-6" onClick={() => onChange({ legs: params.legs.map((l, j) => (j === i ? { ...l, ratio: Math.min(10, l.ratio + 1) } : l)) })}>
                    <Plus className="size-3" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" className="size-8 text-slate-400 hover:text-loss sm:size-6" onClick={() => onChange({ legs: params.legs.filter((_, j) => j !== i) })}>
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="qty">Lots</Label>
          <Input id="qty" type="number" min={1} max={10000} value={params.qty} onChange={(e) => onChange({ qty: Math.max(1, Math.floor(Number(e.target.value) || 1)) })} className="num" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="delta" className={cn(!usesDelta && "opacity-50")}>Short delta</Label>
          <Input id="delta" type="number" step={0.01} min={0.05} max={0.45} disabled={!usesDelta} value={params.shortDelta} onChange={(e) => onChange({ shortDelta: Number(e.target.value) })} className="num" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="wing" className={cn(!usesWings && "opacity-50")}>Wing width %</Label>
          <Input id="wing" type="number" step={0.25} min={0.25} max={15} disabled={!usesWings} value={params.wingWidth} onChange={(e) => onChange({ wingWidth: Number(e.target.value) })} className="num" />
        </div>
      </div>
    </div>
  );
}
