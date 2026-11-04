"use client";

import { useState } from "react";
import { Lightbulb } from "lucide-react";

import { DirectOptionModal } from "@/components/desk/direct-option-modal";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { hours, num, pct, price, signed, signedPct } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { OptionIdea, OptionIdeas } from "@/lib/types";

const TREND = { with: { label: "with trend", variant: "profit" }, against: { label: "against trend", variant: "loss" }, neutral: { label: "no trend", variant: "default" } } as const;

/** The single options worth buying or selling right now: each priced at the side of the book it
 * trades, against what the market is actually moving, with the exit and hold the desk would use.
 * "Use" opens the single-option ticket on that contract and side, sized to the capital. */
export function OptionIdeasCard({ underlying, expiry }: { underlying: string; expiry: number }) {
  const { data, error, isLoading } = useApi<OptionIdeas>(`/market/ideas?underlying=${underlying}&expiry=${expiry}`, { refreshInterval: 15_000 });
  const [chosen, setChosen] = useState<OptionIdea | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Lightbulb className="size-4 text-primary" /> Best options now
        </CardTitle>
        {data && (
          <span className="text-[11.5px] text-muted-foreground">
            market {data.lean} · moving at {pct(data.vol_buyer, 0, true)}–{pct(data.vol_seller, 0, true)} a year · {data.considered} liquid contracts checked
          </span>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        <LoadError error={error} />
        {isLoading && !data && <Loading rows={3} />}
        {data && !data.ideas.length && <Empty title="Nothing worth taking">{data.note}</Empty>}
        {data && data.ideas.length > 0 && (
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[920px] text-[12px]">
              <thead className="bg-slate-50 text-[11px] text-muted-foreground">
                <tr className="text-left">
                  <th className="px-3 py-2 font-medium">Do this</th>
                  <th className="px-2 py-2 text-right font-medium">At</th>
                  <th className="px-2 py-2 text-right font-medium">Worth</th>
                  <th className="px-2 py-2 text-right font-medium">Edge / lot</th>
                  <th className="px-2 py-2 text-right font-medium" title="sorted by this, highest first">Win chance ↓</th>
                  <th className="px-2 py-2 text-right font-medium">Max loss / lot</th>
                  <th className="px-2 py-2 font-medium">Exit</th>
                  <th className="px-2 py-2 text-right font-medium">Hold</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-separator">
                {data.ideas.map((i) => {
                  const trend = TREND[i.trend];
                  return (
                    <tr key={`${i.symbol}-${i.side}`} className="align-top">
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          <Badge variant={i.side === "buy" ? "profit" : "loss"}>{i.side === "buy" ? "BUY" : "SELL"}</Badge>
                          <span className="num font-semibold">{num(i.strike, 0)}</span>
                          <span className="text-muted-foreground">{i.kind}</span>
                        </div>
                        <div className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                          <span>{i.view}</span>
                          <Badge variant={trend.variant}>{trend.label}</Badge>
                        </div>
                      </td>
                      <td className="num px-2 py-2 text-right">{price(i.price)}</td>
                      <td className="num px-2 py-2 text-right text-muted-foreground" title={`fair value at ${pct(i.vol_used, 0, true)} realised volatility`}>
                        {price(i.fair)}
                      </td>
                      <td className="num px-2 py-2 text-right text-profit">
                        <div className="font-semibold">{signed(i.edge_per_lot, 3)}</div>
                        <div className="text-[11px] opacity-80">{signedPct(i.edge_on_risk_pct, 1)} of risk</div>
                      </td>
                      <td className="num px-2 py-2 text-right">{pct(i.pop, 0, true)}</td>
                      <td className="num px-2 py-2 text-right">{i.max_loss_per_lot === null ? <span className="text-loss">unlimited</span> : num(i.max_loss_per_lot, 3)}</td>
                      <td className="max-w-[260px] px-2 py-2 text-[11.5px] leading-snug text-muted-foreground">
                        {i.exit}
                        {i.p_target !== null && <span> · reached before expiry {pct(i.p_target, 0, true)}</span>}
                      </td>
                      <td className="num px-2 py-2 text-right">{i.hold_hours === null ? "to expiry" : `~${hours(i.hold_hours)}`}</td>
                      <td className="px-3 py-2 text-right">
                        <Button size="xs" variant="outline" onClick={() => setChosen(i)}>
                          Use
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-[11px] text-muted-foreground">
          Edge = what the option is worth if the market keeps moving as it has (a buyer judged on the slowest measure, a seller on the fastest) minus the
          price and both fees. Win chance is at expiry, before fees. Hold is an estimate: for a buy, how long a typical move takes to reach the take profit;
          for a sell, how long time decay takes if price stays. Estimates from the numbers, not promises.
        </p>
      </CardContent>
      <DirectOptionModal
        // Keyed on the contract and side, so each "Use" opens fresh on what was clicked.
        key={chosen ? `${chosen.symbol}-${chosen.side}` : "none"}
        target={chosen && { symbol: chosen.symbol, kind: chosen.kind, strike: chosen.strike, mark: chosen.mark, delta: chosen.delta, iv: chosen.iv, bid: chosen.bid, ask: chosen.ask }}
        initialSide={chosen?.side}
        underlying={underlying}
        spotPrice={data?.spot}
        open={chosen !== null}
        onOpenChange={(open) => !open && setChosen(null)}
      />
    </Card>
  );
}
