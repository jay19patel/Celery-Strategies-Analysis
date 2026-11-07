"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { ArrowDownRight, ArrowUpRight, Calculator, Check, Loader2, Play, ShieldAlert, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, requestId } from "@/lib/api";
import { num, pct, price } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Broker, BrokerStatus, OptionKind, Preview, Side, Structure } from "@/lib/types";

export interface OptionLegTarget {
  symbol: string;
  kind: OptionKind;
  strike: number;
  mark: number | null;
  delta: number | null;
  iv: number | null;
  bid: number | null;
  ask: number | null;
}

export function DirectOptionModal({
  target,
  initialSide = "buy",
  underlying = "BTC",
  spotPrice = 0,
  open,
  onOpenChange,
}: {
  target: OptionLegTarget | null;
  initialSide?: Side;
  underlying?: string;
  spotPrice?: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { mutate } = useSWRConfig();
  const { data: sysStatus } = useApi<{ brokers: BrokerStatus[] }>("/system/status");

  const brokers = useMemo<BrokerStatus[]>(() => {
    return sysStatus?.brokers ?? [
      { name: "paper", active: true, ready: true, not_ready: "", auto: true, halted: "", real_money: false, env: "paper" },
      { name: "delta", active: false, ready: false, not_ready: "keys not set", auto: false, halted: "", real_money: false, env: "demo" },
    ];
  }, [sysStatus]);

  const activeBrokers = brokers.filter((b) => b.active);
  const [userBroker, setUserBroker] = useState<Broker | null>(null);
  const [userSide, setUserSide] = useState<Side | null>(null);
  const [userQty, setUserQty] = useState<number | null>(null); // null: sized to the capital
  const [executing, setExecuting] = useState(false);

  const broker = userBroker && activeBrokers.some((b) => b.name === userBroker)
    ? userBroker
    : (activeBrokers[0]?.name as Broker) ?? "paper";
  const side = userSide ?? initialSide;

  // Sized as the strategy builder and the auto-trader size a trade: the worst case is
  // `risk_per_trade_pct` of the broker's equity, at most `max_lots`. The suggestion does not depend
  // on the quantity, so it is asked for at one lot under a key the quantity never changes; the
  // quantity is then derived, never set. (Asking at the quantity itself made a loop: quantity ->
  // request -> suggestion -> quantity, which re-rendered without end whenever two answers disagreed.)
  const leg = target ? { underlying, strategy: "custom", legs: [{ symbol: target.symbol, side, ratio: 1 }] } : null;
  const fetchPreview = ([path, b]: [string, string]) => api<Preview>(path, "POST", JSON.parse(b));
  const unit = useSWR<Preview>(open && leg ? ["/strategies/preview", JSON.stringify({ ...leg, qty: 1 })] : null, fetchPreview, { shouldRetryOnError: false });
  const sizing = unit.data?.sizing.find((s) => s.broker === broker);
  const suggested = sizing?.suggested_qty != null ? Math.max(1, sizing.suggested_qty) : null;
  const qty = userQty ?? suggested ?? 1;
  const priced = useSWR<Preview>(open && leg ? ["/strategies/preview", JSON.stringify({ ...leg, qty })] : null, fetchPreview, {
    keepPreviousData: true,
    shouldRetryOnError: false,
  });
  const analysis = priced.data?.qty === qty ? priced.data.analysis : null;
  const atQty = priced.data?.qty === qty ? priced.data.sizing.find((s) => s.broker === broker) : undefined;

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      setUserSide(null);
      setUserBroker(null);
      setUserQty(null);
    }
    onOpenChange(nextOpen);
  };

  if (!target) return null;

  const mark = target.mark ?? (side === "buy" ? target.ask : target.bid) ?? 0;
  const isCall = target.kind === "call";
  const isBuy = side === "buy";
  const strike = target.strike;
  const spot = spotPrice || strike;

  // Dollar figures from the backend's own analysis: premium x contract size x lots, with fees.
  const premium = analysis ? Math.abs(analysis.net_premium) : null;
  const breakeven = analysis?.breakevens[0] ?? (isCall ? strike + mark : Math.max(0, strike - mark));
  const money = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `$${num(v, 4)}`);
  const maxProfitText = !analysis ? "—" : analysis.max_profit === null ? "Unlimited" : money(analysis.max_profit);
  const maxLossText = !analysis ? "—" : analysis.max_loss === null ? "Unlimited" : `-${money(analysis.max_loss)}`;

  const ruleText = isBuy
    ? isCall
      ? `Profits if ${underlying} > $${num(breakeven, 0)} at expiry`
      : `Profits if ${underlying} < $${num(breakeven, 0)} at expiry`
    : isCall
    ? `Keeps full premium if ${underlying} stays ≤ $${num(strike, 0)}`
    : `Keeps full premium if ${underlying} stays ≥ $${num(strike, 0)}`;

  async function handleExecute() {
    if (!target) return;
    setExecuting(true);
    try {
      const res = await api<Structure>("/trading/open", "POST", {
        broker,
        underlying,
        strategy: "custom",
        legs: [{ symbol: target.symbol, side, ratio: 1 }],
        qty,
        request_id: requestId(),
      });

      if (res.status === "open") {
        toast.success(`Option ${side.toUpperCase()} executed!`, {
          description: `${qty}x ${target.symbol} on ${broker}. View in Positions →`,
        });
        mutate("/trading/structures?status=open");
        mutate("/portfolio/accounts");
        onOpenChange(false);
      } else {
        toast.error(`Order not opened (${res.status})`, {
          description: res.error || res.close_reason,
        });
      }
    } catch (err: unknown) {
      toast.error("Execution failed", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setExecuting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <DialogTitle className="text-base font-bold font-mono">{target.symbol}</DialogTitle>
              <Badge variant={isCall ? "primary" : "warn"} className="uppercase text-[10px]">
                {target.kind}
              </Badge>
            </div>
            <span className="font-mono text-xs text-muted-foreground">Strike: ${num(target.strike, 0)}</span>
          </div>
          <DialogDescription className="text-xs">
            Direct single-leg option execution with live Greeks and risk statistics.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2 text-xs">
          {/* Side Toggle */}
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => { setUserSide("buy"); setUserQty(null); }}
              className={`flex items-center justify-center gap-1.5 rounded-lg border py-2 font-bold transition-colors ${
                isBuy
                  ? "border-profit bg-profit-soft text-profit"
                  : "border-input bg-card text-muted-foreground hover:bg-muted"
              }`}
            >
              <ArrowUpRight className="size-4 text-profit" /> BUY (Long {target.kind.toUpperCase()})
            </button>
            <button
              type="button"
              onClick={() => { setUserSide("sell"); setUserQty(null); }}
              className={`flex items-center justify-center gap-1.5 rounded-lg border py-2 font-bold transition-colors ${
                !isBuy
                  ? "border-loss bg-loss-soft text-loss"
                  : "border-input bg-card text-muted-foreground hover:bg-muted"
              }`}
            >
              <ArrowDownRight className="size-4 text-loss" /> SELL (Short {target.kind.toUpperCase()})
            </button>
          </div>

          {/* Pricing Strip */}
          <div className="grid grid-cols-4 gap-2 rounded-lg bg-secondary/60 p-2.5 text-center font-mono">
            <div>
              <div className="text-[10px] text-muted-foreground uppercase">Mark</div>
              <div className="font-bold text-foreground">${target.mark != null ? price(target.mark) : "—"}</div>
            </div>
            <div>
              <div className="text-[10px] text-muted-foreground uppercase">Delta</div>
              <div className="font-medium text-foreground">{target.delta != null ? target.delta.toFixed(2) : "—"}</div>
            </div>
            <div>
              <div className="text-[10px] text-muted-foreground uppercase">IV</div>
              <div className="font-medium text-foreground">{target.iv != null ? `${(target.iv * 100).toFixed(1)}%` : "—"}</div>
            </div>
            <div>
              <div className="text-[10px] text-muted-foreground uppercase">Spot</div>
              <div className="font-medium text-foreground">${num(spot, 0)}</div>
            </div>
          </div>

          {/* Broker and Quantity */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Broker</Label>
              <Select value={broker} onValueChange={(v) => { setUserBroker(v as Broker); setUserQty(null); }}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {activeBrokers.map((b) => (
                    <SelectItem key={b.name} value={b.name} className="text-xs">
                      {b.name === "paper" ? "Paper Trading" : `Delta Exchange (${b.env})`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label className="text-xs">Contracts / Lots</Label>
                {userQty !== null && (
                  <button
                    type="button"
                    onClick={() => setUserQty(null)}
                    className="flex items-center gap-1 text-[11px] font-semibold text-primary hover:underline"
                  >
                    <Calculator className="size-3" /> Fit my capital
                  </button>
                )}
              </div>
              <Input
                type="number"
                min={1}
                value={qty}
                onChange={(e) => setUserQty(Math.max(1, parseInt(e.target.value) || 1))}
                className="h-8 font-mono text-xs"
              />
            </div>
          </div>

          {sizing && !sizing.error && (
            <p className="text-[11px] text-muted-foreground">
              {userQty === null ? "Sized to your capital" : "Your quantity"}: equity ${num(sizing.equity, 2)}, worst case{" "}
              {atQty?.worst_case_pct != null ? pct(atQty.worst_case_pct, 2) : analysis ? "unlimited" : "…"} of it
              {suggested !== null && userQty !== null && ` (capital fits ${suggested})`}
              {atQty?.margin_ok === false && <span className="text-loss"> · not enough margin for {qty}</span>}
            </p>
          )}
          {sizing?.error && <p className="text-[11px] text-loss">{sizing.error}</p>}

          {/* Statistical Breakdown Box */}
          <div className="space-y-2 rounded-xl bg-secondary/60 p-3">
            <div className="flex justify-between border-b pb-1.5 text-xs">
              <span className="text-muted-foreground">{isBuy ? "Net Premium Outlay" : "Upfront Premium Received"}:</span>
              <span className="font-mono font-bold text-foreground">{money(premium)}</span>
            </div>
            <div className="flex justify-between border-b pb-1.5 text-xs">
              <span className="text-muted-foreground">Breakeven at Expiry:</span>
              <span className="font-mono font-bold text-foreground">${num(breakeven, 0)}</span>
            </div>
            <div className="flex justify-between border-b pb-1.5 text-xs">
              <span className="text-muted-foreground">Max Profit:</span>
              <span className="font-mono font-semibold text-profit">{maxProfitText}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-muted-foreground">Max Loss:</span>
              <span className="font-mono font-semibold text-loss">{maxLossText}</span>
            </div>
            <div className="rounded bg-muted/40 p-1.5 text-[11px] text-muted-foreground">
              💡 {ruleText}
            </div>
          </div>
        </div>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} className="text-xs">
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={handleExecute}
            disabled={executing || mark <= 0}
            className={`gap-1.5 text-xs ${!isBuy ? "bg-loss hover:bg-loss/90" : ""}`}
          >
            {executing ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            {isBuy ? `Buy ${qty}x Option` : `Sell (Short) ${qty}x Option`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
