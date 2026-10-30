"use client";

import { Fragment, useState } from "react";
import { Zap } from "lucide-react";

import { DirectOptionModal, type OptionLegTarget } from "@/components/desk/direct-option-modal";
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

/** Calls on the left, puts on the right. A bid sells that option, an ask buys it.
 * Clicking a quote selects it for multi-leg strategies.
 * Clicking the '⚡' action button opens the Direct Single-Leg Trade Modal.
 */
export function ChainTable({
  underlying,
  expiry,
  picked,
  onPick,
}: {
  underlying: string;
  expiry: number;
  picked: Map<string, Side>;
  onPick: (symbol: string, side: Side) => void;
}) {
  const { data, error, isLoading } = useApi<Chain>(
    `/market/chain?underlying=${underlying}&expiry=${expiry}`,
    { refreshInterval: 10_000 },
  );

  const [directTarget, setDirectTarget] = useState<OptionLegTarget | null>(null);
  const [directSide, setDirectSide] = useState<Side>("buy");
  const [modalOpen, setModalOpen] = useState(false);
  const [filterSide, setFilterSide] = useState<"both" | "calls" | "puts">("both");

  if (error) return <LoadError error={error} className="m-4" />;
  if (isLoading && !data) return <Loading rows={8} className="p-4" />;
  if (!data?.rows.length) return <Empty title="No strikes near spot for this expiry" />;

  const atm = data.rows.reduce(
    (best, r) => (Math.abs(r.strike - data.spot) < Math.abs(best.strike - data.spot) ? r : best),
    data.rows[0],
  ).strike;

  const openDirectTrade = (sideData: ChainSide, kind: "call" | "put", strike: number, s: Side) => {
    setDirectTarget({
      symbol: sideData.symbol,
      kind,
      strike,
      mark: sideData.mark,
      delta: sideData.delta,
      iv: sideData.iv,
      bid: sideData.bid,
      ask: sideData.ask,
    });
    setDirectSide(s);
    setModalOpen(true);
  };

  const quote = (
    sideData: ChainSide | undefined,
    which: "bid" | "ask",
    kind: "call" | "put",
    strike: number,
  ) => {
    if (!sideData) return <td className="px-1 py-1 sm:px-2 sm:py-1.5" />;
    const value = sideData[which];
    const trade: Side = which === "bid" ? "sell" : "buy";
    const chosen = picked.get(sideData.symbol) === trade;
    return (
      <td className="px-0.5 py-1 sm:px-1">
        <button
          disabled={value === null}
          onClick={() => onPick(sideData.symbol, trade)}
          onDoubleClick={(e) => {
            e.stopPropagation();
            openDirectTrade(sideData, kind, strike, trade);
          }}
          title={
            value === null
              ? "no quote on this side"
              : `Click to add ${trade} to structure, Double-click for Direct Order (${trade} ${sideData.symbol} at ${price(value)})`
          }
          className={cn(
            "num w-full rounded px-1 sm:px-1.5 py-1 sm:py-0.5 text-right text-[11px] sm:text-[12px] font-medium transition-all active:scale-95 disabled:cursor-not-allowed disabled:opacity-40",
            which === "bid" ? "text-loss hover:bg-loss-soft" : "text-profit hover:bg-profit-soft",
            chosen &&
              (which === "bid" ? "bg-loss text-white hover:bg-loss shadow-2xs" : "bg-profit text-white hover:bg-profit shadow-2xs"),
          )}
        >
          {value === null ? "—" : price(value)}
        </button>
      </td>
    );
  };

  const showCalls = filterSide === "both" || filterSide === "calls";
  const showPuts = filterSide === "both" || filterSide === "puts";
  const colSpan = (showCalls && showPuts) ? 13 : 7;

  return (
    <>
      {/* Mobile Segmented Toggle for compact mobile trading */}
      <div className="flex items-center justify-between border-b px-3 py-2 bg-slate-50/70 sm:hidden">
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Side:</span>
        <div className="flex rounded-lg border bg-slate-200/60 p-0.5 text-xs shadow-2xs">
          <button
            type="button"
            onClick={() => setFilterSide("both")}
            className={cn(
              "rounded-md px-2.5 py-1 text-[11px] font-semibold transition-all active:scale-95",
              filterSide === "both" ? "bg-white text-slate-900 shadow-xs" : "text-slate-600 hover:text-slate-900",
            )}
          >
            All
          </button>
          <button
            type="button"
            onClick={() => setFilterSide("calls")}
            className={cn(
              "rounded-md px-2.5 py-1 text-[11px] font-semibold transition-all active:scale-95",
              filterSide === "calls" ? "bg-profit text-white shadow-xs" : "text-slate-600 hover:text-slate-900",
            )}
          >
            Calls
          </button>
          <button
            type="button"
            onClick={() => setFilterSide("puts")}
            className={cn(
              "rounded-md px-2.5 py-1 text-[11px] font-semibold transition-all active:scale-95",
              filterSide === "puts" ? "bg-loss text-white shadow-xs" : "text-slate-600 hover:text-slate-900",
            )}
          >
            Puts
          </button>
        </div>
      </div>

      <div className="max-h-[560px] overflow-auto no-scrollbar">
        <table className={cn("w-full text-[11.5px] sm:text-[12px]", filterSide === "both" ? "min-w-[760px]" : "min-w-full")}>
          <thead className="sticky top-0 z-10 bg-slate-50 text-[10.5px] sm:text-[11px] text-muted-foreground shadow-2xs">
            {filterSide === "both" && (
              <tr className="border-b">
                <th colSpan={6} className="py-1 text-center font-semibold text-slate-600">
                  Calls
                </th>
                <th className="border-x bg-slate-100 py-1 text-center font-semibold text-slate-600">
                  Strike
                </th>
                <th colSpan={6} className="py-1 text-center font-semibold text-slate-600">
                  Puts
                </th>
              </tr>
            )}
            <tr className="border-b">
              {showCalls && (
                <>
                  <th className="w-6 px-1 py-1.5 text-center">⚡</th>
                  {["OI", "IV", "Δ", "Bid", "Ask"].map((h) => (
                    <th key={`c${h}`} className="px-1.5 sm:px-2 py-1.5 text-right font-medium">
                      {h}
                    </th>
                  ))}
                </>
              )}

              <th className="border-x bg-slate-100 px-2 py-1.5 text-center font-semibold text-slate-700">
                Strike
              </th>

              {showPuts && (
                <>
                  {["Bid", "Ask", "Δ", "IV", "OI"].map((h) => (
                    <th key={`p${h}`} className="px-1.5 sm:px-2 py-1.5 text-right font-medium">
                      {h}
                    </th>
                  ))}
                  <th className="w-6 px-1 py-1.5 text-center">⚡</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => {
              const callItm = r.strike < data.spot;
              return (
                <Fragment key={r.strike}>
                  {r.strike === atm && (
                    <tr>
                      <td
                        colSpan={colSpan}
                        className="bg-accent py-0.5 text-center text-[10.5px] font-semibold text-accent-foreground"
                      >
                        spot {price(data.spot)}
                      </td>
                    </tr>
                  )}
                  <tr className="border-b border-slate-100 hover:bg-slate-50/60">
                    {/* Call Columns */}
                    {showCalls && (
                      <>
                        <td className="px-1 py-1 text-center">
                          {r.call ? (
                            <button
                              type="button"
                              onClick={() => openDirectTrade(r.call!, "call", r.strike, "buy")}
                              title={`Direct trade Call ${r.strike}`}
                              className="rounded p-1 text-slate-400 hover:bg-primary/10 hover:text-primary transition-colors active:scale-90"
                            >
                              <Zap className="size-3" />
                            </button>
                          ) : null}
                        </td>

                        <td className={cn("num px-1.5 sm:px-2 py-1 sm:py-1.5 text-right text-muted-foreground", callItm && "bg-amber-50/50")}>
                          {num(r.call?.oi, 2)}
                        </td>
                        <td className={cn("num px-1.5 sm:px-2 py-1 sm:py-1.5 text-right", callItm && "bg-amber-50/50")}>
                          {pct(r.call?.iv, 1, true)}
                        </td>
                        <td className={cn("num px-1.5 sm:px-2 py-1 sm:py-1.5 text-right text-muted-foreground", callItm && "bg-amber-50/50")}>
                          {num(r.call?.delta, 2)}
                        </td>
                        {quote(r.call, "bid", "call", r.strike)}
                        {quote(r.call, "ask", "call", r.strike)}
                      </>
                    )}

                    {/* Strike Column */}
                    <td className="num border-x bg-slate-50 px-2 sm:px-3 py-1 sm:py-1.5 text-center font-semibold text-slate-800">
                      {price(r.strike)}
                    </td>

                    {/* Put Columns */}
                    {showPuts && (
                      <>
                        {quote(r.put, "bid", "put", r.strike)}
                        {quote(r.put, "ask", "put", r.strike)}
                        <td className={cn("num px-1.5 sm:px-2 py-1 sm:py-1.5 text-right text-muted-foreground", !callItm && "bg-amber-50/50")}>
                          {num(r.put?.delta, 2)}
                        </td>
                        <td className={cn("num px-1.5 sm:px-2 py-1 sm:py-1.5 text-right", !callItm && "bg-amber-50/50")}>
                          {pct(r.put?.iv, 1, true)}
                        </td>
                        <td className={cn("num px-1.5 sm:px-2 py-1 sm:py-1.5 text-right text-muted-foreground", !callItm && "bg-amber-50/50")}>
                          {num(r.put?.oi, 2)}
                        </td>
                        <td className="px-1 py-1 text-center">
                          {r.put ? (
                            <button
                              type="button"
                              onClick={() => openDirectTrade(r.put!, "put", r.strike, "buy")}
                              title={`Direct trade Put ${r.strike}`}
                              className="rounded p-1 text-slate-400 hover:bg-primary/10 hover:text-primary transition-colors active:scale-90"
                            >
                              <Zap className="size-3" />
                            </button>
                          ) : null}
                        </td>
                      </>
                    )}
                  </tr>
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Direct Single Leg Order Modal */}
      <DirectOptionModal
        target={directTarget}
        initialSide={directSide}
        underlying={underlying}
        spotPrice={data.spot}
        open={modalOpen}
        onOpenChange={setModalOpen}
      />
    </>
  );
}
