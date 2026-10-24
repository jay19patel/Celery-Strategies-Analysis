"use client";

import { Fragment } from "react";

import { Empty, LoadError, Loading } from "@/components/shared/states";
import { num, pct, price } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Chain, ChainSide, Side } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface PickedLeg {
  symbol: string;
  side: Side;
  ratio: number;
}

/** Calls on the left, puts on the right. A bid sells that option, an ask buys it. */
export function ChainTable({ underlying, expiry, picked, onPick }: { underlying: string; expiry: number; picked: Map<string, Side>; onPick: (symbol: string, side: Side) => void }) {
  const { data, error, isLoading } = useApi<Chain>(`/market/chain?underlying=${underlying}&expiry=${expiry}`, { refreshInterval: 10_000 });
  if (error) return <LoadError error={error} className="m-4" />;
  if (isLoading && !data) return <Loading rows={8} className="p-4" />;
  if (!data?.rows.length) return <Empty title="No strikes near spot for this expiry" />;

  const atm = data.rows.reduce((best, r) => (Math.abs(r.strike - data.spot) < Math.abs(best.strike - data.spot) ? r : best), data.rows[0]).strike;

  const quote = (side: ChainSide | undefined, which: "bid" | "ask") => {
    if (!side) return <td className="px-2 py-1.5" />;
    const value = side[which];
    const trade: Side = which === "bid" ? "sell" : "buy";
    const chosen = picked.get(side.symbol) === trade;
    return (
      <td className="px-1 py-1">
        <button
          disabled={value === null}
          onClick={() => onPick(side.symbol, trade)}
          title={value === null ? "no quote on this side" : `${trade} ${side.symbol} at ${price(value)}`}
          className={cn(
            "num w-full rounded px-1.5 py-0.5 text-right transition-colors disabled:cursor-not-allowed disabled:opacity-40",
            which === "bid" ? "text-loss hover:bg-loss-soft" : "text-profit hover:bg-profit-soft",
            chosen && (which === "bid" ? "bg-loss text-white hover:bg-loss" : "bg-profit text-white hover:bg-profit"),
          )}
        >
          {value === null ? "—" : price(value)}
        </button>
      </td>
    );
  };

  return (
    <div className="max-h-[560px] overflow-auto">
      <table className="w-full min-w-[760px] text-[12px]">
        <thead className="sticky top-0 z-10 bg-slate-50 text-[11px] text-muted-foreground">
          <tr className="border-b">
            <th colSpan={5} className="py-1.5 text-center font-semibold text-slate-600">Calls</th>
            <th className="border-x bg-slate-100 py-1.5 text-center font-semibold text-slate-600">Strike</th>
            <th colSpan={5} className="py-1.5 text-center font-semibold text-slate-600">Puts</th>
          </tr>
          <tr className="border-b">
            {["OI", "IV", "Δ", "Bid", "Ask"].map((h) => (
              <th key={`c${h}`} className="px-2 py-1.5 text-right font-medium">{h}</th>
            ))}
            <th className="border-x bg-slate-100 px-2 py-1.5 text-center font-medium" />
            {["Bid", "Ask", "Δ", "IV", "OI"].map((h) => (
              <th key={`p${h}`} className="px-2 py-1.5 text-right font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r) => {
            const callItm = r.strike < data.spot;
            return (
              <Fragment key={r.strike}>
                {r.strike === atm && (
                  <tr>
                    <td colSpan={11} className="bg-accent py-0.5 text-center text-[10.5px] font-semibold text-accent-foreground">
                      spot {price(data.spot)}
                    </td>
                  </tr>
                )}
                <tr className="border-b border-slate-100 hover:bg-slate-50/60">
                  <td className={cn("num px-2 py-1.5 text-right text-muted-foreground", callItm && "bg-amber-50/50")}>{num(r.call?.oi, 2)}</td>
                  <td className={cn("num px-2 py-1.5 text-right", callItm && "bg-amber-50/50")}>{pct(r.call?.iv, 1, true)}</td>
                  <td className={cn("num px-2 py-1.5 text-right text-muted-foreground", callItm && "bg-amber-50/50")}>{num(r.call?.delta, 2)}</td>
                  {quote(r.call, "bid")}
                  {quote(r.call, "ask")}
                  <td className="num border-x bg-slate-50 px-3 py-1.5 text-center font-semibold">{price(r.strike)}</td>
                  {quote(r.put, "bid")}
                  {quote(r.put, "ask")}
                  <td className={cn("num px-2 py-1.5 text-right text-muted-foreground", !callItm && "bg-amber-50/50")}>{num(r.put?.delta, 2)}</td>
                  <td className={cn("num px-2 py-1.5 text-right", !callItm && "bg-amber-50/50")}>{pct(r.put?.iv, 1, true)}</td>
                  <td className={cn("num px-2 py-1.5 text-right text-muted-foreground", !callItm && "bg-amber-50/50")}>{num(r.put?.oi, 2)}</td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
