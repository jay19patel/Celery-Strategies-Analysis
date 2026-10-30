"use client";

import { useState } from "react";
import Link from "next/link";
import { Send } from "lucide-react";
import { toast } from "sonner";

import { Confirm } from "@/components/shared/confirm";
import { Pnl, Premium } from "@/components/shared/money";
import { Notice } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, requestId } from "@/lib/api";
import { pct, usd } from "@/lib/format";
import type { Broker, BrokerStatus, Preview, Structure } from "@/lib/types";

export function OrderPanel({ preview, brokers, onQty }: { preview: Preview; brokers: BrokerStatus[]; onQty: (q: number) => void }) {
  const active = brokers.filter((b) => b.active);
  const [chosen, setBroker] = useState<Broker | "">("");
  const broker: Broker | "" = active.some((b) => b.name === chosen) ? chosen : (active[0]?.name ?? "");
  const [reqId, setReqId] = useState(requestId);

  const status = active.find((b) => b.name === broker);
  const sizing = preview.sizing.find((s) => s.broker === broker);
  const failed = preview.checks.filter((c) => c.required && !c.ok);
  const blocked = !status ? "choose a broker" : status.halted ? `halted: ${status.halted}` : status.not_ready;

  const place = async () => {
    const legs = preview.plan.legs.map((l) => ({ symbol: l.symbol, side: l.side, ratio: l.ratio }));
    try {
      const s = await api<Structure>("/trading/open", "POST", {
        broker, underlying: preview.plan.underlying, strategy: preview.strategy, legs, qty: preview.qty, request_id: reqId,
      });
      if (s.status === "open") toast.success(`${s.label} opened on ${s.broker}`, { description: <Link href="/positions">View in Positions →</Link> });
      else toast.error(`Not opened (${s.status})`, { description: s.error || s.close_reason });
      setReqId(requestId());
    } catch (e) {
      toast.error("Order refused", { description: e instanceof Error ? e.message : String(e) });
      throw e;
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5 w-full sm:w-auto">
          <Label>Broker</Label>
          <Select value={broker} onValueChange={(v) => setBroker(v as Broker)}>
            <SelectTrigger className="w-full sm:w-44">
              <SelectValue placeholder="Choose broker" />
            </SelectTrigger>
            <SelectContent>
              {active.map((b) => (
                <SelectItem key={b.name} value={b.name}>
                  {b.name === "paper" ? "Paper" : `Delta (${b.env})`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {sizing && !sizing.error && (
          <div className="text-[12px] sm:text-[12.5px] text-muted-foreground">
            <div>
              Equity <span className="num font-medium text-slate-800">{usd(sizing.equity)}</span> · available{" "}
              <span className="num font-medium text-slate-800">{usd(sizing.available)}</span> · this trade needs{" "}
              <span className={`num font-medium ${sizing.margin_ok === false ? "text-loss" : "text-slate-800"}`}>
                {usd(preview.analysis.margin + preview.analysis.entry_fees)}
              </span>
            </div>
            <div>
              Worst case {sizing.worst_case_pct != null ? <span className="num font-medium text-slate-800">{pct(sizing.worst_case_pct, 2)}</span> : "unlimited"} of equity · risk budget fits{" "}
              <button className="num font-semibold text-primary hover:underline" onClick={() => onQty(Math.max(1, sizing.suggested_qty ?? 1))}>
                {sizing.suggested_qty ?? 0} lots
              </button>
            </div>
          </div>
        )}
        {sizing?.error && <span className="text-[12.5px] text-loss">{sizing.error}</span>}
      </div>

      {status?.real_money && <Notice tone="loss">This is the live Delta account: the order is real money.</Notice>}
      {sizing && sizing.margin_ok === false && <Notice tone="loss">Not enough available margin on {broker} for {preview.qty} lots.</Notice>}

      <Confirm
        title={`Open ${preview.label} × ${preview.qty} on ${broker}?`}
        confirmLabel={status?.real_money ? "Place real order" : "Place order"}
        destructive={status?.real_money}
        onConfirm={place}
        description={
          <div className="space-y-2 text-[13px]">
            <div className="flex justify-between"><span>Premium</span><Premium value={preview.analysis.net_premium} /></div>
            <div className="flex justify-between"><span>Worst case</span>{preview.analysis.max_loss === null ? <span className="font-semibold text-loss">unlimited</span> : <Pnl value={-preview.analysis.max_loss} digits={4} />}</div>
            <div className="flex justify-between"><span>Margin held</span><span className="num">{usd(preview.analysis.margin, 2)}</span></div>
            <div className="flex justify-between"><span>Entry fees</span><span className="num">{usd(preview.analysis.entry_fees, 4)}</span></div>
            <p className="text-muted-foreground">Each leg is sent as an immediate-or-cancel limit at most 5% from its quote; bought legs first, and anything filled is unwound if a leg fails.</p>
            {failed.length > 0 && (
              <Notice tone="warn">
                Fails {failed.map((c) => c.name).join(", ")} — the auto-trader would not take this. You can still place it.
              </Notice>
            )}
          </div>
        }
        trigger={(open) => (
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 w-full sm:w-auto">
            <Button onClick={open} disabled={!!blocked || preview.plan.legs.length === 0} className="w-full sm:w-auto h-10 text-[13.5px] font-semibold active:scale-95 shadow-sm">
              <Send className="size-4" /> Place {preview.qty} lot{preview.qty > 1 ? "s" : ""}
            </Button>
            {blocked && <Badge variant="warn" className="w-fit">{blocked}</Badge>}
          </div>
        )}
      />
    </div>
  );
}
