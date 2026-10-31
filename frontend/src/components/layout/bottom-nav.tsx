"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Layers, LayoutGrid, LineChart, Radar, Sparkles } from "lucide-react";

import { NAV } from "@/components/layout/nav";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useApi, useLive } from "@/lib/live";
import type { Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

const PRIMARY_TABS = [
  { href: "/", label: "Desk", icon: Sparkles },
  { href: "/market", label: "Market", icon: Radar },
  { href: "/positions", label: "Positions", icon: Layers, badge: true },
  { href: "/performance", label: "P&L", icon: LineChart },
];

const isActive = (pathname: string, href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

/** The phone's tab bar: the four pages used most, and every other page in a sheet that slides up. */
export function BottomNav() {
  const pathname = usePathname();
  const { connected } = useLive();
  const [more, setMore] = useState(false);
  const { data: open } = useApi<Structure[]>("/trading/structures?status=open", {
    refreshInterval: 15_000,
  });
  const openCount = open?.length ?? 0;
  const inMore = !PRIMARY_TABS.some((t) => isActive(pathname, t.href));

  const tab = (active: boolean) =>
    cn(
      "relative flex min-h-12 flex-1 flex-col items-center justify-center gap-0.5 rounded-xl transition-colors active:scale-95",
      active ? "text-primary" : "text-slate-500",
    );

  return (
    <>
      <nav
        aria-label="Mobile Navigation"
        className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200/90 bg-white/90 pb-safe shadow-[0_-3px_15px_rgba(15,23,42,0.06)] backdrop-blur-md lg:hidden"
      >
        <div className="mx-auto flex h-16 max-w-xl items-center gap-1 px-2">
          {PRIMARY_TABS.map((t) => {
            const active = isActive(pathname, t.href);
            const Icon = t.icon;
            return (
              <Link key={t.href} href={t.href} className={tab(active)} aria-current={active ? "page" : undefined}>
                <span className={cn("relative grid h-7 w-12 place-items-center rounded-full transition-colors duration-300", active && "bg-accent")}>
                  <Icon className={cn("size-5", active ? "stroke-[2.2]" : "stroke-[1.8]")} />
                  {t.badge && openCount > 0 && (
                    <span className="num absolute -top-1 right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[9.5px] font-bold text-white ring-2 ring-white">
                      {openCount}
                    </span>
                  )}
                </span>
                <span className={cn("text-[10.5px] tracking-tight", active && "font-semibold")}>{t.label}</span>
              </Link>
            );
          })}
          <button type="button" onClick={() => setMore(true)} aria-label="More pages" className={tab(inMore)}>
            <span className={cn("grid h-7 w-12 place-items-center rounded-full transition-colors duration-300", inMore && "bg-accent")}>
              <LayoutGrid className={cn("size-5", inMore ? "stroke-[2.2]" : "stroke-[1.8]")} />
            </span>
            <span className={cn("text-[10.5px] tracking-tight", inMore && "font-semibold")}>More</span>
          </button>
        </div>
      </nav>

      <Sheet open={more} onOpenChange={setMore}>
        <SheetContent side="bottom" className="lg:hidden">
          <div className="mx-auto mt-2.5 h-1.5 w-10 shrink-0 rounded-full bg-slate-200" />
          <div className="flex items-center justify-between px-5 pt-3 pb-1">
            <SheetTitle className="text-[15px] font-semibold text-slate-900">All pages</SheetTitle>
            <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span className={cn("size-2 rounded-full", connected ? "animate-pulse bg-profit" : "bg-slate-300")} />
              {connected ? "Live" : "Connecting…"}
            </span>
          </div>
          <div className="space-y-4 overflow-y-auto px-4 pt-2 pb-5">
            {NAV.map((group) => (
              <div key={group.group}>
                <div className="mb-2 px-1 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">{group.group}</div>
                <div className="grid grid-cols-3 gap-2">
                  {group.items.map((item) => {
                    const active = isActive(pathname, item.href);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        onClick={() => setMore(false)}
                        className={cn(
                          "pressable relative flex min-h-[76px] flex-col items-center justify-center gap-1.5 rounded-xl border px-1.5 py-2.5 text-center text-[11.5px] font-medium",
                          active ? "border-primary/30 bg-accent text-accent-foreground" : "border-slate-200 bg-card text-slate-700",
                        )}
                      >
                        <item.icon className={cn("size-5", active ? "text-primary" : "text-slate-500")} />
                        <span className="leading-tight">{item.label}</span>
                        {item.href === "/positions" && openCount > 0 && (
                          <span className="num absolute top-1.5 right-1.5 rounded-full bg-primary px-1.5 text-[10px] font-bold text-white">{openCount}</span>
                        )}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
