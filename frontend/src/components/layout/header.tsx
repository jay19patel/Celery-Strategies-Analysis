"use client";

import { usePathname, useRouter } from "next/navigation";
import { Lock, OctagonX } from "lucide-react";
import { toast } from "sonner";

import { ALL_NAV } from "@/components/layout/nav";
import { Confirm } from "@/components/shared/confirm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { price, signedPct } from "@/lib/format";
import { useApi, useLive } from "@/lib/live";
import type { Accounts } from "@/lib/types";
import { cn } from "@/lib/utils";

export function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const { prices, connected } = useLive();

  const { data: accounts } = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 20_000 });
  const title = ALL_NAV.find((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)))?.label ?? "TradeBuddy";
  const realMoney = accounts?.brokers.some((b) => b.real_money && b.active);

  // On a phone the strip shows one underlying: BTC when there is one.
  const mobileSymbol = "BTC" in prices ? "BTC" : Object.keys(prices)[0];
  const mobileTicker = mobileSymbol ? prices[mobileSymbol] : undefined;

  const closeAll = async () => {
    const r = await api<{ closed: string[]; errors: string[] }>("/trading/close-all", "POST", {});
    if (r.errors.length) toast.error(`${r.closed.length} closed, ${r.errors.length} failed`, { description: r.errors.join("\n") });
    else toast.success(r.closed.length ? `${r.closed.length} structure(s) closed` : "Nothing was open");
  };

  const lock = async () => {
    await api("/auth/logout", "POST");
    router.replace("/login");
  };

  return (
    <header className="sticky top-0 z-30 border-b bg-white/90 pt-safe shadow-[0_1px_3px_rgba(0,0,0,0.02)] backdrop-blur-md">
      <div className="flex h-14 items-center gap-2 px-4 sm:gap-3 lg:px-6">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="truncate text-[15px] font-semibold text-slate-900">{title}</h1>
          <span
            className={cn(
              "size-2 shrink-0 rounded-full lg:hidden",
              connected ? "bg-profit shadow-[0_0_0_2px_rgba(14,138,62,0.2)] animate-pulse" : "bg-slate-300",
            )}
            title={connected ? "WebSocket Connected" : "Connecting..."}
          />
        </div>

        {mobileTicker && (
          <div className="num flex min-w-0 shrink items-baseline gap-1.5 rounded-full border border-slate-200/60 bg-slate-100/90 px-2.5 py-1 text-[11px] md:hidden">
            <span className="font-bold text-slate-500">{mobileSymbol}</span>
            <span className="truncate font-semibold text-slate-900">{price(mobileTicker.spot)}</span>
            <span className={cn("hidden font-medium min-[400px]:inline", (mobileTicker.change_24h_pct ?? 0) >= 0 ? "text-profit" : "text-loss")}>
              {signedPct(mobileTicker.change_24h_pct)}
            </span>
          </div>
        )}

        {/* Desktop ticker strip */}
        <div className="hidden items-center gap-4 border-l pl-4 md:flex">
          {Object.entries(prices).map(([u, p]) => (
            <div key={u} className="num flex items-baseline gap-1.5 text-xs">
              <span className="font-semibold text-slate-500">{u}</span>
              <span className="text-[13px] font-semibold">{price(p.spot)}</span>
              <span className={cn("text-[11px] font-medium", (p.change_24h_pct ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                {signedPct(p.change_24h_pct)}
              </span>
            </div>
          ))}
        </div>

        <div className="flex-1" />

        {realMoney && (
          <Badge variant="solid" className="text-[10px] sm:text-xs px-1.5 py-0.5 font-bold tracking-wider">
            LIVE
          </Badge>
        )}

        <Confirm
          title="Close every open structure?"
          description="Every open structure on every broker is closed now at the live book. Auto-trading stays as it is."
          confirmLabel="Close all"
          destructive
          onConfirm={() => closeAll().catch((e) => toast.error(e.message))}
          trigger={(open) => (
            <Button variant="destructive-outline" size="sm" onClick={open} className="h-9 px-2.5 text-xs sm:h-8 sm:px-3" aria-label="Close all">
              <OctagonX className="size-3.5" /> <span className="hidden sm:inline">Close all</span>
            </Button>
          )}
        />

        <Button variant="outline" size="sm" onClick={lock} title="Lock the dashboard" className="h-9 px-2.5 text-xs sm:h-8 sm:px-3" aria-label="Lock">
          <Lock className="size-3.5" /> <span className="hidden sm:inline">Lock</span>
        </Button>
      </div>
    </header>
  );
}
