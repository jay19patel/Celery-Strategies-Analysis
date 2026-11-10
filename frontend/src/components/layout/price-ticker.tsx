"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";

import { num, price, signedPct } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { LivePrice } from "@/lib/types";
import { cn } from "@/lib/utils";

type Dir = "up" | "down";

const compactUsd = (v: number | null) =>
  v === null ? "—" : `$${Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v)}`;

function Metric({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="num font-medium text-foreground">{children}</span>
    </span>
  );
}

/** One underlying: its price (flashing green or red on each tick), the 24h move, and the book's numbers. */
function Item({ symbol, p, dir, tick, stale }: { symbol: string; p: LivePrice; dir?: Dir; tick: number; stale: boolean }) {
  const chg = p.change_24h_pct;
  const up = (chg ?? 0) >= 0;
  return (
    <Link
      href="/market"
      tabIndex={-1}
      className="inline-flex shrink-0 items-center gap-4 whitespace-nowrap border-r border-separator px-5 text-[12.5px] leading-none transition-colors hover:bg-secondary/60"
    >
      <span className="inline-flex items-center gap-2">
        <span className="font-semibold tracking-[0.01em]">{symbol}</span>
        <span className={cn("relative rounded-md px-1 font-semibold", stale && "text-muted-foreground")}>
          {dir && !stale && <span key={tick} aria-hidden className={cn("absolute inset-0 rounded-md", dir === "up" ? "ticker-flash-up" : "ticker-flash-down")} />}
          {/* Plain digits: rolling digits inside a strip that is itself sliding read as overlapping text. */}
          <span className="num relative">{p.spot === null || stale ? "—" : price(p.spot)}</span>
        </span>
        {stale ? (
          <span className="rounded-full bg-warn-soft px-1.5 text-[11px] font-semibold text-warn">stale</span>
        ) : (
          chg !== null && (
            <span className={cn("num inline-flex items-center gap-px font-medium", up ? "text-profit" : "text-loss")}>
              {up ? <ArrowUpRight className="size-3" strokeWidth={2.5} /> : <ArrowDownRight className="size-3" strokeWidth={2.5} />}
              {signedPct(chg)}
            </span>
          )
        )}
      </span>
      {!stale && (
        <span className="inline-flex items-center gap-4">
          {p.high_24h !== null && p.low_24h !== null && (
            <Metric label="24h">
              {price(p.low_24h)} – {price(p.high_24h)}
            </Metric>
          )}
          {p.atm_iv !== null && <Metric label="ATM IV">{(p.atm_iv * 100).toFixed(1)}%</Metric>}
          {p.funding_rate_pct !== null && (
            <Metric label="Funding">
              <span className={p.funding_rate_pct > 0 ? "text-loss" : p.funding_rate_pct < 0 ? "text-profit" : undefined}>
                {(p.funding_rate_pct * 100).toFixed(4)}%
              </span>
            </Metric>
          )}
          {p.pcr_oi !== null && <Metric label="P/C OI">{num(p.pcr_oi, 2)}</Metric>}
          {p.max_pain !== null && <Metric label="Max pain">{price(p.max_pain)}</Metric>}
          {p.oi_usd !== null && <Metric label="OI">{compactUsd(p.oi_usd)}</Metric>}
        </span>
      )}
    </Link>
  );
}

/**
 * The live tape under the header: every underlying's price and book, scrolling like a news ticker.
 * The list is drawn twice and slides by half its width, so the loop has no seam. It pauses under the
 * pointer or a finger. While the socket is down the last prices are marked stale, never shown as
 * current (absence over staleness).
 */
export function PriceTicker() {
  const { prices, connected } = useLive();
  const symbols = Object.keys(prices).sort();

  // Which way each price last moved, worked out when new prices arrive.
  const [prev, setPrev] = useState(prices);
  const [moves, setMoves] = useState<{ tick: number; dirs: Record<string, Dir> }>({ tick: 0, dirs: {} });
  if (prices !== prev) {
    const dirs: Record<string, Dir> = {};
    for (const s of Object.keys(prices)) {
      const a = prev[s]?.spot, b = prices[s]?.spot;
      if (a != null && b != null && a !== b) dirs[s] = b > a ? "up" : "down";
    }
    setPrev(prices);
    setMoves((m) => ({ tick: m.tick + 1, dirs }));
  }

  // A steady reading speed whatever the content's width.
  const track = useRef<HTMLDivElement>(null);
  const [seconds, setSeconds] = useState(40);
  useEffect(() => {
    const el = track.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSeconds(Math.max(30, el.scrollWidth / 2 / 30))); // 30px a second: readable
    ro.observe(el);
    return () => ro.disconnect();
  }, [symbols.length]);


  // Enough copies of a short list to fill a wide screen before the loop repeats.
  const copies = Math.max(1, Math.ceil(4 / symbols.length));
  const row = (hidden: boolean) =>
    Array.from({ length: copies }, (_, c) =>
      symbols.map((s) => (
        <span key={`${c}-${s}`} aria-hidden={hidden || c > 0 || undefined} className="contents">
          <Item symbol={s} p={prices[s]} dir={moves.dirs[s]} tick={moves.tick} stale={!connected} />
        </span>
      )),
    );

  return (
    <div className="material fixed inset-x-0 top-0 z-40 border-b border-separator pt-safe">
      {symbols.length ? (
        <div className="ticker relative flex h-8 items-stretch overflow-hidden" aria-label="Live prices" role="marquee">
          <div ref={track} className="ticker-track flex items-stretch" style={{ animationDuration: `${seconds}s` }}>
            <div className="flex items-stretch">{row(false)}</div>
            <div className="flex items-stretch">{row(true)}</div>
          </div>
        </div>
      ) : (
        <div className="flex h-8 items-center gap-2 px-5 text-[12.5px] text-muted-foreground">
          <span className={cn("size-2 rounded-full", connected ? "bg-profit" : "bg-slate-300")} />
          {connected ? "Waiting for the first prices…" : "Connecting to live prices…"}
        </div>
      )}
    </div>
  );
}
