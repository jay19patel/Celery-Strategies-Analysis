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

export function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const { prices } = useLive();
  const [menu, setMenu] = useState(false);
  const { data: accounts } = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 20_000 });
  const title = ALL_NAV.find((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)))?.label ?? "TradeBuddy";
  const realMoney = accounts?.brokers.some((b) => b.real_money && b.active);

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
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-white/90 px-4 backdrop-blur lg:px-6">
      <Button variant="ghost" size="icon-sm" className="lg:hidden" onClick={() => setMenu(true)} aria-label="Menu">
        <Menu />
      </Button>
      <Sheet open={menu} onOpenChange={setMenu}>
        <SheetContent>
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SidebarBody onNavigate={() => setMenu(false)} />
        </SheetContent>
      </Sheet>
      <h1 className="truncate text-[15px] font-semibold">{title}</h1>
      <div className="hidden items-center gap-4 border-l pl-4 md:flex">
        {Object.entries(prices).map(([u, p]) => (
          <div key={u} className="num flex items-baseline gap-1.5 text-xs">
            <span className="font-semibold text-slate-500">{u}</span>
            <span className="text-[13px] font-semibold">{price(p.spot)}</span>
            <span className={cn("text-[11px] font-medium", (p.change_24h_pct ?? 0) >= 0 ? "text-profit" : "text-loss")}>{signedPct(p.change_24h_pct)}</span>
          </div>
        ))}
      </div>
      <div className="flex-1" />
      {realMoney && <Badge variant="solid">REAL MONEY</Badge>}
      <Confirm
        title="Close every open structure?"
        description="Every open structure on every broker is closed now at the live book. Auto-trading stays as it is."
        confirmLabel="Close all"
        destructive
        onConfirm={() => closeAll().catch((e) => toast.error(e.message))}
        trigger={(open) => (
          <Button variant="destructive-outline" size="sm" onClick={open}>
            <OctagonX /> <span className="hidden sm:inline">Close all</span>
          </Button>
        )}
      />
      <Button variant="outline" size="sm" onClick={lock} title="Lock the dashboard">
        <Lock /> <span className="hidden sm:inline">Lock</span>
      </Button>
    </header>
  );
}
