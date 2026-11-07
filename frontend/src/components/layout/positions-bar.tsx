"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronRight, ChevronUp, Layers } from "lucide-react";

import { Rolling } from "@/components/shared/rolling";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { arrowSigned, hours, pnlClass, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Account, Accounts, Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

/** The open book in one place: what is open, what closing it now would book, and how the day stands. */
function useBook() {
  const { data: open } = useApi<Structure[]>("/trading/structures?status=open", { refreshInterval: 5_000 });
  const { data: accounts } = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 10_000 });
  const list = open ?? [];
  // Valued the way the Positions page values them: what closing every leg now would book.
  const priced = list.filter((s) => s.live?.pnl != null);
  const unrealised = priced.reduce((a, s) => a + (s.live?.pnl ?? 0), 0);
  let today: number | null = null;
  let margin = 0;
  for (const acc of Object.values(accounts?.accounts ?? {})) {
    if (!acc || "error" in acc) continue;
    const a = acc as Account;
    margin += a.margin_used;
    const start = accounts?.today[a.broker]?.start_equity;
    if (start != null) today = (today ?? 0) + a.equity - start;
  }
  return { list, loaded: open !== undefined, unrealised: priced.length ? unrealised : null, unpriced: list.length - priced.length, today, margin };
}

const Money = ({ v, className }: { v: number | null; className?: string }) => (
  <span className={cn("num font-semibold", pnlClass(v), className)}>{v === null ? "—" : <Rolling text={arrowSigned(v)} />}</span>
);

function TradeRow({ s, onNavigate }: { s: Structure; onNavigate?: () => void }) {
  const pnl = s.live?.pnl ?? null;
  return (
    <Link href="/positions" onClick={onNavigate} className="group flex min-h-14 items-center gap-3 pl-4 transition-colors active:bg-secondary">
      <span className="flex min-h-14 flex-1 items-center gap-3 border-b border-separator pr-4 group-last:border-0">
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-[15px] font-medium">{s.label}</span>
            <Badge variant={s.broker === "delta" ? "primary" : "default"}>{s.broker}</Badge>
          </span>
          <span className="num block truncate text-[12.5px] text-muted-foreground">
            {s.underlying} · {s.instrument === "perp" ? s.kind : s.expiry_label} · {s.instrument === "perp" ? `${s.qty} lots` : `${hours(s.hours_left)} left`}
          </span>
        </span>
        <span className="text-right">
          <Money v={pnl} className="block text-[15px]" />
          {pnl === null && <span className="block text-[11px] text-warn">no fresh quote</span>}
        </span>
        <ChevronRight className="size-4 shrink-0 text-slate-300" />
      </span>
    </Link>
  );
}

/** Desktop: a slim bar along the bottom with the totals and a chip per open trade. */
function DesktopBar() {
  const b = useBook();
  if (!b.loaded) return null;
  return (
    <div className="material fixed inset-x-0 bottom-0 z-30 hidden h-10 items-center gap-5 border-t border-separator px-6 text-[13px] lg:left-64 lg:flex lg:px-8">
      <Link href="/positions" className="inline-flex items-center gap-2 font-semibold hover:text-primary">
        <Layers className="size-4 text-primary" />
        {b.list.length ? `${b.list.length} open` : "No open trades"}
      </Link>
      <span className="inline-flex items-baseline gap-1.5">
        <span className="text-muted-foreground">Unrealised</span>
        <Money v={b.unrealised} />
        {b.unpriced > 0 && <span className="text-[11.5px] text-warn">({b.unpriced} unpriced)</span>}
      </span>
      <span className="inline-flex items-baseline gap-1.5">
        <span className="text-muted-foreground">Today</span>
        <Money v={b.today} />
      </span>
      <span className="inline-flex items-baseline gap-1.5">
        <span className="text-muted-foreground">Margin</span>
        <span className="num font-medium">{usd(b.margin)}</span>
      </span>
      <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto no-scrollbar">
        {b.list.map((s) => (
          <Link
            key={s.id}
            href="/positions"
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-secondary px-2.5 py-1 text-[12px] transition-colors hover:bg-secondary/70"
            title={`${s.label} · ${s.broker} · ${s.underlying} ${s.expiry_label}`}
          >
            <span className="font-medium">{s.label}</span>
            <span className="text-muted-foreground">{s.underlying}</span>
            <Money v={s.live?.pnl ?? null} />
          </Link>
        ))}
      </div>
      <Link href="/positions" className="inline-flex shrink-0 items-center gap-0.5 font-medium text-primary">
        Positions <ChevronRight className="size-4" />
      </Link>
    </div>
  );
}

/** Phone: a floating bar above the tab bar, like a mini player; tapping it lists every open trade. */
function PhoneBar() {
  const b = useBook();
  const [open, setOpen] = useState(false);
  if (!b.list.length) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="material fixed inset-x-2 bottom-[calc(50px+env(safe-area-inset-bottom)+8px)] z-40 flex h-14 items-center gap-3 rounded-2xl border border-separator px-3.5 text-left active:scale-[0.98] lg:hidden"
        style={{ transition: "transform 160ms cubic-bezier(0.23, 1, 0.32, 1)" }}
        aria-label={`${b.list.length} open trades`}
      >
        <span className="grid size-9 shrink-0 place-items-center rounded-[10px] bg-[#30b0c7] text-white">
          <Layers className="size-[18px]" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-semibold leading-tight">
            {b.list.length} open trade{b.list.length === 1 ? "" : "s"}
          </span>
          <span className="block truncate text-[12.5px] text-muted-foreground">
            Today <Money v={b.today} className="font-medium" />
            {b.unpriced > 0 && <span className="text-warn"> · {b.unpriced} unpriced</span>}
          </span>
        </span>
        <span className="text-right">
          <span className="block text-[11px] text-muted-foreground">Unrealised</span>
          <Money v={b.unrealised} className="text-[16px]" />
        </span>
        <ChevronUp className="size-4 shrink-0 text-muted-foreground" />
      </button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="bottom" className="lg:hidden">
          <div className="mx-auto mt-2 h-[5px] w-9 shrink-0 rounded-full bg-slate-300" />
          <div className="flex items-end justify-between px-5 pt-3 pb-3">
            <div>
              <SheetTitle className="text-[20px] font-bold tracking-[-0.02em]">Open trades</SheetTitle>
              <div className="text-[13px] text-muted-foreground">
                Today <Money v={b.today} className="font-medium" /> · margin <span className="num">{usd(b.margin)}</span>
              </div>
            </div>
            <div className="text-right">
              <div className="text-[12px] text-muted-foreground">Unrealised</div>
              <Money v={b.unrealised} className="text-[20px]" />
            </div>
          </div>
          <div className="overflow-y-auto px-4 pb-5">
            <div className="overflow-hidden rounded-xl bg-card">
              {b.list.map((s) => (
                <TradeRow key={s.id} s={s} onNavigate={() => setOpen(false)} />
              ))}
            </div>
            <Link
              href="/positions"
              onClick={() => setOpen(false)}
              className="mt-3 flex h-12 items-center justify-center rounded-xl bg-primary text-[15px] font-semibold text-primary-foreground active:opacity-80"
            >
              Open Positions
            </Link>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}

export function PositionsBar() {
  return (
    <>
      <DesktopBar />
      <PhoneBar />
    </>
  );
}
