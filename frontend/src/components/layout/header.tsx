"use client";

import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Lock, Menu, OctagonX } from "lucide-react";
import { toast } from "sonner";

import { ALL_NAV } from "@/components/layout/nav";
import { SidebarBody } from "@/components/layout/sidebar";
import { Confirm } from "@/components/shared/confirm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import { price, signedPct } from "@/lib/format";
import { useApi, useLive } from "@/lib/live";
import type { Accounts } from "@/lib/types";
import { cn } from "@/lib/utils";

interface HeaderProps {
  menuOpen?: boolean;
  onMenuOpenChange?: (open: boolean) => void;
}

export function Header({ menuOpen, onMenuOpenChange }: HeaderProps = {}) {
  const pathname = usePathname();
  const router = useRouter();
  const { prices, connected } = useLive();
  const [internalMenu, setInternalMenu] = useState(false);

  const isMenuOpen = menuOpen !== undefined ? menuOpen : internalMenu;
  const setMenu = onMenuOpenChange ?? setInternalMenu;

  const { data: accounts } = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 20_000 });
  const title = ALL_NAV.find((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)))?.label ?? "TradeBuddy";
  const realMoney = accounts?.brokers.some((b) => b.real_money && b.active);

  // Default mobile price ticker for the first underlying (BTC or ETH)
  const mobileTicker = prices["BTC"] ?? Object.values(prices)[0];

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
    <header className="sticky top-0 z-30 flex h-14 items-center gap-2.5 border-b bg-white/95 px-3 backdrop-blur-md sm:gap-3 sm:px-4 lg:px-6 shadow-[0_1px_3px_rgba(0,0,0,0.02)]">
      <Button
        variant="ghost"
        size="icon-sm"
        className="lg:hidden active:scale-95"
        onClick={() => setMenu(true)}
        aria-label="Open Navigation Drawer"
      >
        <Menu className="size-5" />
      </Button>

      <Sheet open={isMenuOpen} onOpenChange={setMenu}>
        <SheetContent>
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SidebarBody onNavigate={() => setMenu(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex items-center gap-2 min-w-0">
        <h1 className="truncate text-[14.5px] sm:text-[15px] font-semibold text-slate-900">{title}</h1>
        {/* Live indicator dot for mobile */}
        <span
          className={cn(
            "size-2 shrink-0 rounded-full lg:hidden",
            connected ? "bg-profit shadow-[0_0_0_2px_rgba(14,138,62,0.2)] animate-pulse" : "bg-slate-300",
          )}
          title={connected ? "WebSocket Connected" : "Connecting..."}
        />
      </div>

      {/* Mobile Live Ticker pill */}
      {mobileTicker && (
        <div className="flex md:hidden items-center gap-1.5 rounded-md bg-slate-100/90 px-2 py-0.5 text-[11px] font-medium border border-slate-200/60 max-w-[130px] sm:max-w-none truncate">
          <span className="font-bold text-slate-700 text-[10.5px]">BTC</span>
          <span className="num font-semibold text-slate-900 text-[11px]">{price(mobileTicker.spot)}</span>
          <span className={cn("num font-medium text-[10px]", (mobileTicker.change_24h_pct ?? 0) >= 0 ? "text-profit" : "text-loss")}>
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
          <Button variant="destructive-outline" size="sm" onClick={open} className="h-8 px-2 sm:px-3 text-xs active:scale-95">
            <OctagonX className="size-3.5" /> <span className="hidden sm:inline">Close all</span>
          </Button>
        )}
      />

      <Button variant="outline" size="sm" onClick={lock} title="Lock the dashboard" className="h-8 px-2 sm:px-3 text-xs active:scale-95">
        <Lock className="size-3.5" /> <span className="hidden sm:inline">Lock</span>
      </Button>
    </header>
  );
}
