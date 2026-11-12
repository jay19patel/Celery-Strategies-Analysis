"use client";

import { Minus, Plus, Trash2 } from "lucide-react";

import type { PickedLeg } from "@/components/desk/chain-table";
import { ParamsForm } from "@/components/strategies/params-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { StrategyInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

const VIEW_TONE = { neutral: "default", bullish: "profit", bearish: "loss", volatile: "warn", directional: "primary" } as const;

export interface TicketParams {
  strategy: string;
  qty: number;
  /** this ticket's own values for the strategy's params, over what it is configured with */
  overrides: Record<string, number | boolean | string>;
  legs: PickedLeg[];
}

export function StrategyPicker({ strategies, params, onChange }: { strategies: StrategyInfo[]; params: TicketParams; onChange: (p: Partial<TicketParams>) => void }) {
  const spec = strategies.find((s) => s.name === params.strategy);

  return (
    <div className="space-y-4">
      <div className="flex gap-1.5 overflow-x-auto pb-1 no-scrollbar sm:flex-wrap sm:pb-0 -mx-1 px-1">
        {strategies.map((s) => (
          <button
            key={s.name}
            onClick={() => onChange({ strategy: s.name, overrides: {} })}
            className={cn(
              "shrink-0 rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-[color,background-color,transform] duration-150 active:scale-95",
              params.strategy === s.name ? "bg-primary text-primary-foreground" : "bg-secondary text-foreground hover:bg-secondary/70",
            )}
          >
            {s.label}
          </button>
        ))}
        <button
          onClick={() => onChange({ strategy: "custom" })}
          className={cn(
            "shrink-0 rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-[color,background-color,transform] duration-150 active:scale-95",
            params.strategy === "custom" ? "bg-primary text-primary-foreground" : "bg-secondary text-primary hover:bg-secondary/70",
          )}
        >
          Custom legs
        </button>
      </div>

      {spec && (
        <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted-foreground">
          {spec.view && <Badge variant={VIEW_TONE[spec.view]} className="capitalize">{spec.view}</Badge>}
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

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="qty">Lots</Label>
          <Input id="qty" type="number" min={1} max={10000} value={params.qty} onChange={(e) => onChange({ qty: Math.max(1, Math.floor(Number(e.target.value) || 1)) })} className="num" />
        </div>
      </div>
      {spec && (
        <details className="rounded-lg border px-3 py-2">
          <summary className="cursor-pointer select-none text-[13px] font-medium">This ticket&apos;s settings</summary>
          <p className="mt-1 text-[11.5px] text-muted-foreground">Start from how the strategy is configured; a change here applies to this ticket only.</p>
          <div className="mt-3">
            <ParamsForm
              idPrefix="ticket"
              schema={spec.schema}
              values={{ ...spec.params, ...params.overrides }}
              onChange={(name, value) => onChange({ overrides: { ...params.overrides, [name]: value } })}
            />
          </div>
        </details>
      )}
    </div>
  );
}
