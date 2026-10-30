"use client";

import { Stat } from "@/components/shared/stat";
import { Hint } from "@/components/shared/hint";
import { hours, pct, price, signedPct } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { cn } from "@/lib/utils";

export function ExpiryPicker({ overview, expiry, onChange }: { overview: Overview; expiry: number; onChange: (e: number) => void }) {
  return (
    <div className="flex gap-1.5 overflow-x-auto pb-1 no-scrollbar -mx-1 px-1">
      {overview.expiries.map((e) => (
        <button
          key={e.expiry}
          onClick={() => onChange(e.expiry)}
          className={cn(
            "shrink-0 rounded-lg border bg-card px-2.5 sm:px-3 py-1.5 text-left transition-all active:scale-95 hover:border-slate-300 shadow-2xs",
            e.expiry === expiry && "border-primary bg-accent ring-1 ring-primary/20",
          )}
        >
          <div className="text-[12px] sm:text-[12.5px] font-semibold">{e.label}</div>
          <div className="num text-[10px] sm:text-[10.5px] text-muted-foreground">
            {hours(e.hours)} · IV {pct(e.atm_iv, 1, true)}
          </div>
        </button>
      ))}
    </div>
  );
}

export function MarketStats({ overview, expiry }: { overview: Overview; expiry: number }) {
  const e = overview.expiries.find((x) => x.expiry === expiry) ?? overview.expiries[0];
  const rv = overview.realised_vol;
  const premium = e?.atm_iv != null && rv.reference != null ? (e.atm_iv - rv.reference) * 100 : null;
  const change = overview.stats?.change_24h_pct ?? null;
  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 xl:grid-cols-8">
      <Stat label={`${overview.underlying} index`} value={price(overview.spot)} hint={<span className={cn((change ?? 0) >= 0 ? "text-profit" : "text-loss")}>{signedPct(change)} 24h</span>} />
      <Stat label="ATM IV" value={pct(e?.atm_iv, 1, true)} hint={e ? `${e.label} expiry` : undefined} />
      <Stat label="Realised vol" value={pct(rv.reference, 1, true)} hint={`24h ${pct(rv["24h"], 1, true)} · 7d ${pct(rv["7d"], 1, true)}`} />
      <Stat
        label={<span className="flex items-center gap-1">Vol premium <Hint>IV minus realised volatility, in vol points. Positive: options are priced above what the market has been doing, the edge a seller is paid for.</Hint></span>}
        value={premium === null ? "—" : `${premium > 0 ? "+" : ""}${premium.toFixed(1)} pts`}
        valueClass={premium === null ? "" : premium > 0 ? "text-profit" : "text-loss"}
        hint={premium === null ? "" : premium > 0 ? "sellers' edge" : "buyers' edge"}
      />
      <Stat label="Implied move" value={e?.implied_move ? `±${price(e.implied_move)}` : "—"} hint={e?.implied_move_pct ? `±${e.implied_move_pct.toFixed(2)}% to expiry` : undefined} />
      <Stat label="Max pain" value={price(e?.max_pain)} hint={e?.max_pain && overview.spot ? signedPct(((e.max_pain - overview.spot) / overview.spot) * 100) : undefined} />
      <Stat label="Put / call OI" value={e?.pcr_oi?.toFixed(2) ?? "—"} hint={`walls ${price(e?.put_wall)} / ${price(e?.call_wall)}`} />
      <Stat label="25Δ skew" value={e?.skew_25d != null ? `${(e.skew_25d * 100).toFixed(1)} pts` : "—"} hint={e?.skew_25d != null ? (e.skew_25d > 0 ? "puts dearer" : "calls dearer") : undefined} />
    </div>
  );
}
