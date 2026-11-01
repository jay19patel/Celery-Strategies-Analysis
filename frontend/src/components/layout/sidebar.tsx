"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CandlestickChart } from "lucide-react";

import { isActive, NAV } from "@/components/layout/nav";
import { useApi, useLive } from "@/lib/live";
import type { Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

function SidebarBody() {
  const pathname = usePathname();
  const { connected } = useLive();
  const { data: open } = useApi<Structure[]>("/trading/structures?status=open", { refreshInterval: 15_000 });

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center gap-2.5 px-5">
        <div className="grid size-8 place-items-center rounded-[9px] bg-gradient-to-b from-[#3395ff] to-[#0071e3] text-white shadow-[0_1px_2px_rgb(0_0_0/0.15)]">
          <CandlestickChart className="size-[18px]" />
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-semibold tracking-[-0.01em]">TradeBuddy</div>
          <div className="text-[11px] text-muted-foreground">Options & Perps desk</div>
        </div>
      </div>
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 pt-2 pb-4 no-scrollbar">
        {NAV.map((group) => (
          <div key={group.group}>
            <div className="mb-1 px-2.5 text-[12px] font-semibold text-muted-foreground">{group.group}</div>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(pathname, item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex h-9 items-center gap-2.5 rounded-[9px] px-2 text-[14px] font-medium transition-colors duration-100",
                      active ? "bg-primary text-white" : "text-foreground hover:bg-secondary",
                    )}
                  >
                    <span className={cn("grid size-6 shrink-0 place-items-center rounded-[7px] text-white", item.tint)}>
                      <item.icon className="size-[15px]" />
                    </span>
                    <span className="truncate">{item.label}</span>
                    {item.href === "/positions" && !!open?.length && (
                      <span className={cn("num ml-auto min-w-5 rounded-full px-1.5 text-center text-[12px] font-semibold", active ? "bg-white/25 text-white" : "text-muted-foreground")}>
                        {open.length}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="flex items-center gap-2 px-5 py-3.5 text-[12px] text-muted-foreground">
        <span className={cn("size-2 rounded-full", connected ? "bg-profit" : "bg-slate-300")} />
        {connected ? "Live" : "Connecting…"}
      </div>
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="fixed top-(--ticker-h) bottom-0 left-0 z-30 hidden w-64 border-r border-separator bg-sidebar backdrop-blur-xl lg:block">
      <SidebarBody />
    </aside>
  );
}
