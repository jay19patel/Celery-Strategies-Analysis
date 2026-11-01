"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight, Layers, LayoutGrid, LineChart, Radar, Sparkles } from "lucide-react";

import { isActive, NAV } from "@/components/layout/nav";
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
    cn("relative flex h-full flex-1 flex-col items-center justify-center gap-[3px] pt-1", active ? "text-primary" : "text-[#8e8e93]");

  return (
    <>
      <nav aria-label="Main" className="material fixed inset-x-0 bottom-0 z-40 border-t border-separator pb-safe lg:hidden">
        <div className="mx-auto flex h-[50px] max-w-xl items-stretch">
          {PRIMARY_TABS.map((t) => {
            const active = isActive(pathname, t.href);
            const Icon = t.icon;
            return (
              <Link key={t.href} href={t.href} className={tab(active)} aria-current={active ? "page" : undefined}>
                <span className="relative">
                  <Icon className={cn("size-6", active ? "stroke-[2.1]" : "stroke-[1.7]")} />
                  {t.badge && openCount > 0 && (
                    <span className="num absolute -top-1 -right-2.5 flex h-[17px] min-w-[17px] items-center justify-center rounded-full bg-[#ff3b30] px-1 text-[11px] font-semibold text-white">
                      {openCount}
                    </span>
                  )}
                </span>
                <span className="text-[10px] font-medium tracking-[0.01em]">{t.label}</span>
              </Link>
            );
          })}
          <button type="button" onClick={() => setMore(true)} aria-label="More pages" className={tab(inMore)}>
            <LayoutGrid className={cn("size-6", inMore ? "stroke-[2.1]" : "stroke-[1.7]")} />
            <span className="text-[10px] font-medium tracking-[0.01em]">More</span>
          </button>
        </div>
      </nav>

      <Sheet open={more} onOpenChange={setMore}>
        <SheetContent side="bottom" className="lg:hidden">
          <div className="mx-auto mt-2 h-[5px] w-9 shrink-0 rounded-full bg-slate-300" />
          <div className="flex items-center justify-between px-5 pt-3 pb-2">
            <SheetTitle className="text-[20px] font-bold tracking-[-0.02em]">All pages</SheetTitle>
            <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
              <span className={cn("size-2 rounded-full", connected ? "bg-profit" : "bg-slate-300")} />
              {connected ? "Live" : "Connecting…"}
            </span>
          </div>
          <div className="space-y-5 overflow-y-auto px-4 pt-1 pb-5">
            {NAV.map((group) => (
              <div key={group.group}>
                <div className="mb-1.5 px-4 text-[13px] uppercase text-muted-foreground">{group.group}</div>
                <div className="overflow-hidden rounded-xl bg-card">
                  {group.items.map((item) => {
                    const active = isActive(pathname, item.href);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        onClick={() => setMore(false)}
                        aria-current={active ? "page" : undefined}
                        className="group flex min-h-11 items-center gap-3 pl-4 transition-colors active:bg-secondary"
                      >
                        <span className={cn("grid size-[29px] shrink-0 place-items-center rounded-[7px] text-white", item.tint)}>
                          <item.icon className="size-[17px]" />
                        </span>
                        <span className="flex min-h-11 flex-1 items-center gap-2 border-b border-separator pr-4 group-last:border-0">
                          <span className={cn("flex-1 text-[16px]", active && "font-semibold text-primary")}>{item.label}</span>
                          {item.href === "/positions" && openCount > 0 && <span className="num text-[15px] text-muted-foreground">{openCount}</span>}
                          <ChevronRight className="size-4 text-slate-300" />
                        </span>
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
