"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CandlestickChart } from "lucide-react";

import { NAV } from "@/components/layout/nav";
import { useApi, useLive } from "@/lib/live";
import type { Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

function SidebarBody() {
  const pathname = usePathname();
  const { connected } = useLive();
  const { data: open } = useApi<Structure[]>("/trading/structures?status=open", { refreshInterval: 15_000 });

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center border-b px-5">
        <div className="flex items-center gap-2.5">
          <div className="grid size-7 place-items-center rounded-lg bg-primary text-white shadow-xs">
            <CandlestickChart className="size-4" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-slate-900">TradeBuddy</div>
            <div className="text-[10.5px] text-muted-foreground">Options & Perps desk</div>
          </div>
        </div>
      </div>
      <nav className="flex-1 space-y-4 overflow-y-auto px-3 py-4 no-scrollbar">
        {NAV.map((group) => (
          <div key={group.group}>
            <div className="mb-1.5 px-3 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">{group.group}</div>
            <div className="space-y-1">
              {group.items.map((item) => {
                const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      "flex min-h-[42px] items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] font-medium text-slate-600 transition-all active:scale-[0.98] hover:bg-slate-50 hover:text-slate-900",
                      active && "bg-accent text-accent-foreground font-semibold hover:bg-accent hover:text-accent-foreground shadow-2xs",
                    )}
                  >
                    <item.icon className={cn("size-4 shrink-0", active ? "text-primary" : "text-slate-400")} />
                    <span className="truncate">{item.label}</span>
                    {item.href === "/positions" && !!open?.length && (
                      <span className="num ml-auto rounded-full bg-primary/10 text-primary px-2 py-0.5 text-[11px] font-bold">
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
      <div className="flex items-center gap-2 border-t px-5 py-3 text-[11.5px] text-muted-foreground">
        <span className={cn("size-2 rounded-full", connected ? "bg-profit shadow-[0_0_0_3px_rgba(14,138,62,0.15)] animate-pulse" : "bg-slate-300")} />
        {connected ? "WebSocket Connected" : "Connecting…"}
      </div>
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r bg-sidebar lg:block">
      <SidebarBody />
    </aside>
  );
}
