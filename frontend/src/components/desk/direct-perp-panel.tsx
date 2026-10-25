"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowDownRight,
  ArrowUpRight,
  Calculator,
  CheckCircle2,
  HelpCircle,
  Loader2,
  Play,
  RotateCcw,
  ShieldAlert,
  Sparkles,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, requestId } from "@/lib/api";
import { num, price, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Broker, BrokerStatus, MarketStructure, Side } from "@/lib/types";

interface SizingRow {
  broker: string;
  equity?: number;
  available?: number;
  suggested_qty?: number | null;
  error?: string;
}

interface TicketResponse {
  symbol: string;
  price: number | null;
  contract_value: number;
  fee_rate: number;
  sizing: SizingRow[];
}

export function DirectPerpPanel({
  underlying,
  currentPrice,
  structure,
}: {
  underlying: string;
  currentPrice: number;
  structure: MarketStructure | null | undefined;
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
  const [side, setSide] = useState<Side>("buy");
  const [qty, setQty] = useState<number>(1);
  const [userStop, setUserStop] = useState<number | null>(null);
  const [userTarget, setUserTarget] = useState<number | null>(null);
  const [executing, setExecuting] = useState(false);

  const broker = userBroker && activeBrokers.some((b) => b.name === userBroker)
    ? userBroker
    : (activeBrokers[0]?.name as Broker) ?? "paper";

  const atr = structure?.atr || (currentPrice > 0 ? currentPrice * 0.015 : 100);
  const defaultStop = currentPrice > 0
    ? Math.round((side === "buy" ? currentPrice - 1.5 * atr : currentPrice + 1.5 * atr) * 10) / 10
    : 0;
  const defaultTarget = currentPrice > 0
    ? Math.round((side === "buy" ? currentPrice + 3.0 * atr : currentPrice - 3.0 * atr) * 10) / 10
    : 0;

  const stop = userStop ?? defaultStop;
  const target = userTarget ?? defaultTarget;

  // Fetch sizing from backend risk engine when stop and side change
  const { data: ticket } = useApi<TicketResponse>(
    currentPrice > 0 && stop > 0
      ? `/trading/perp/ticket?underlying=${underlying}&side=${side}&stop=${stop}`
      : null,
  );

  const activeSizing = ticket?.sizing?.find((s) => s.broker === broker);
  const contractValue = ticket?.contract_value ?? (underlying === "BTC" ? 0.001 : 0.01);

  // Auto-calculate statistical stop, target, and sizing (no AI - pure market statistics)
  const autoFillFromStats = () => {
    if (!currentPrice || currentPrice <= 0) return;
    const atrVal = structure?.atr || currentPrice * 0.015;

    let autoStop = 0;
    let autoTarget = 0;

    if (side === "buy") {
      // For BUY: Stop at nearest support below, or 1.5 ATR down
      const nearestSupport = structure?.nearby?.below?.[0]?.price;
      autoStop = nearestSupport && nearestSupport < currentPrice ? nearestSupport : currentPrice - 1.5 * atrVal;
      // Target: Nearest resistance above, or 3.0 ATR up (1:2 R:R)
      const nearestRes = structure?.nearby?.above?.[0]?.price;
      autoTarget = nearestRes && nearestRes > currentPrice ? nearestRes : currentPrice + 3.0 * atrVal;
    } else {
      // For SELL: Stop at nearest resistance above, or 1.5 ATR up
      const nearestRes = structure?.nearby?.above?.[0]?.price;
      autoStop = nearestRes && nearestRes > currentPrice ? nearestRes : currentPrice + 1.5 * atrVal;
      // Target: Nearest support below, or 3.0 ATR down (1:2 R:R)
      const nearestSupport = structure?.nearby?.below?.[0]?.price;
      autoTarget = nearestSupport && nearestSupport < currentPrice ? nearestSupport : currentPrice - 3.0 * atrVal;
    }

    setUserStop(Math.round(autoStop * 10) / 10);
    setUserTarget(Math.round(autoTarget * 10) / 10);

    if (activeSizing?.suggested_qty && activeSizing.suggested_qty > 0) {
      setQty(activeSizing.suggested_qty);
    } else {
      setQty(1);
    }

    toast.info("Auto-set statistical levels", {
      description: `Stop & Target set from ${structure?.atr ? "ATR(14) & Confluence levels" : "market volatility"}`,
    });
  };

  // Risk and Reward calculations
  const riskDist = currentPrice > 0 && stop > 0 ? Math.abs(currentPrice - stop) : 0;
  const rewardDist = currentPrice > 0 && target > 0 ? Math.abs(target - currentPrice) : 0;
  const rrRatio = riskDist > 0 ? rewardDist / riskDist : 0;
  const estDollarRisk = riskDist * qty * contractValue;
  const estDollarReward = rewardDist * qty * contractValue;

  async function handleOpenPosition() {
    if (currentPrice <= 0 || stop <= 0 || target <= 0) {
      toast.error("Stop and Target prices are required");
      return;
    }
    if (side === "buy" && stop >= currentPrice) {
      toast.error("Buy stop-loss must be below entry price");
      return;
    }
    if (side === "sell" && stop <= currentPrice) {
      toast.error("Sell stop-loss must be above entry price");
      return;
    }

    setExecuting(true);
    try {
      const res = await api<Record<string, unknown>>("/trading/perp", "POST", {
        broker,
        underlying,
        side,
        qty,
        stop,
        target,
        request_id: requestId(),
      });

      toast.success(`${underlying} ${side.toUpperCase()} opened!`, {
        description: (
          <span>
            {qty}x {underlying} on {broker} with stop ${num(stop, 1)}.{" "}
            <Link href="/positions" className="font-semibold underline">
              View Positions →
            </Link>
          </span>
        ),
      });

      mutate("/trading/structures?status=open");
      mutate("/portfolio/accounts");
      mutate("/portfolio/stats?broker=" + broker);
    } catch (err: unknown) {
      toast.error("Order rejected", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setExecuting(false);
    }
  }

  const isBuy = side === "buy";

  return (
    <Card className="border-border/80 shadow-sm">
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <div className="space-y-0.5">
          <CardTitle className="text-sm font-bold flex items-center gap-2">
            <Zap className="size-4 text-primary" /> Direct {underlying} Perpetual Trade
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Execute manual trades with automated statistical risk management (no AI required).
          </p>
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={autoFillFromStats}
          className="h-8 gap-1.5 text-xs text-primary border-primary/30 hover:bg-primary/5"
        >
          <Calculator className="size-3.5" /> Set Automatic
        </Button>
      </CardHeader>

      <CardContent className="space-y-4 pt-1 text-xs">
        {/* Side Selector */}
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => {
              setSide("buy");
              setUserStop(null);
              setUserTarget(null);
            }}
            className={`flex items-center justify-center gap-2 rounded-lg border py-2.5 font-bold transition-all ${
              isBuy
                ? "border-emerald-500 bg-emerald-50 text-emerald-800 shadow-sm"
                : "border-input bg-card text-muted-foreground hover:bg-muted"
            }`}
          >
            <ArrowUpRight className="size-4 text-emerald-600" /> BUY / LONG
          </button>

          <button
            type="button"
            onClick={() => {
              setSide("sell");
              setUserStop(null);
              setUserTarget(null);
            }}
            className={`flex items-center justify-center gap-2 rounded-lg border py-2.5 font-bold transition-all ${
              !isBuy
                ? "border-rose-500 bg-rose-50 text-rose-800 shadow-sm"
                : "border-input bg-card text-muted-foreground hover:bg-muted"
            }`}
          >
            <ArrowDownRight className="size-4 text-rose-600" /> SELL / SHORT
          </button>
        </div>

        {/* Broker & Quantity */}
        {/* Broker Execution */}
        <div className="space-y-1.5">
          <Label className="text-xs font-medium text-muted-foreground">Broker Execution</Label>
          <Select value={broker} onValueChange={(v) => setUserBroker(v as Broker)}>
            <SelectTrigger className="h-8 text-xs font-medium w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {activeBrokers.map((b) => (
                <SelectItem key={b.name} value={b.name} className="text-xs">
                  {b.name === "paper" ? "Paper Account (Simulation)" : `Delta Exchange (${b.env})`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Quantity (Lots) & Entry Price Grid */}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-medium">Contracts (Lots)</Label>
            </div>
            <Input
              type="number"
              min={1}
              max={1000}
              value={qty}
              onChange={(e) => setQty(Math.max(1, parseInt(e.target.value) || 1))}
              className="h-8 font-mono text-xs"
            />
            {activeSizing?.suggested_qty != null && (
              <button
                type="button"
                onClick={() => setQty(activeSizing.suggested_qty!)}
                className="text-[10px] font-semibold text-primary hover:underline block text-left"
              >
                Fits risk: {activeSizing.suggested_qty} lots
              </button>
            )}
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs font-medium text-muted-foreground">Entry Price</Label>
            <div className="h-8 flex items-center px-2.5 rounded-md border bg-muted/30 font-mono text-xs font-bold text-foreground">
              ${currentPrice ? usd(currentPrice, 1) : "—"}
            </div>
            <span className="text-[10px] text-muted-foreground block">
              Market IOC
            </span>
          </div>
        </div>

        {/* Stop Loss & Take Profit Grid */}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-semibold text-rose-600">Stop Loss ($)</Label>
              <span className="text-[10px] text-muted-foreground font-mono">
                {riskDist > 0 ? `-${((riskDist / currentPrice) * 100).toFixed(1)}%` : ""}
              </span>
            </div>
            <Input
              type="number"
              step="0.5"
              value={stop || ""}
              onChange={(e) => setUserStop(parseFloat(e.target.value) || 0)}
              className="h-8 font-mono text-xs border-rose-200 focus-visible:ring-rose-500"
              placeholder="0.0"
            />
          </div>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-semibold text-emerald-600">Take Profit ($)</Label>
              <span className="text-[10px] text-muted-foreground font-mono">
                {rewardDist > 0 ? `+${((rewardDist / currentPrice) * 100).toFixed(1)}%` : ""}
              </span>
            </div>
            <Input
              type="number"
              step="0.5"
              value={target || ""}
              onChange={(e) => setUserTarget(parseFloat(e.target.value) || 0)}
              className="h-8 font-mono text-xs border-emerald-200 focus-visible:ring-emerald-500"
              placeholder="0.0"
            />
          </div>
        </div>

        {/* Statistical Summary Box */}
        <div className="rounded-lg border bg-muted/30 p-3 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">Risk : Reward</span>
              <Badge variant={rrRatio >= 1.5 ? "profit" : "default"} className="font-mono text-[10px]">
                {rrRatio > 0 ? `${rrRatio.toFixed(1)}:1` : "—"}
              </Badge>
            </div>

            <div className="flex items-center gap-4 text-xs font-mono">
              <span className="text-rose-600">Max Risk: ~${num(estDollarRisk, 2)}</span>
              <span className="text-emerald-600">Est Gain: ~${num(estDollarReward, 2)}</span>
            </div>
          </div>

          <div className="flex items-center justify-between border-t pt-2 text-[11px] text-muted-foreground">
            <span>
              Contract Value: <span className="font-mono font-medium text-foreground">{contractValue} {underlying}</span>
            </span>
            <span>
              ATR(14): <span className="font-mono font-medium text-foreground">${structure?.atr ? num(structure.atr, 1) : "—"}</span>
            </span>
          </div>
        </div>

        {/* Execute Button */}
        <Button
          type="button"
          onClick={handleOpenPosition}
          disabled={executing || currentPrice <= 0 || stop <= 0 || target <= 0}
          className={`w-full gap-2 font-bold text-xs ${
            !isBuy ? "bg-rose-600 hover:bg-rose-700" : "bg-emerald-600 hover:bg-emerald-700"
          }`}
        >
          {executing ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
          {isBuy ? `Open ${underlying} Long Position` : `Open ${underlying} Short Position`}
        </Button>
      </CardContent>
    </Card>
  );
}
