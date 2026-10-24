"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CandlestickChart } from "lucide-react";

import { NAV } from "@/components/layout/nav";
import { useApi, useLive } from "@/lib/live";
import type { Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

export function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { connected } = useLive();
  const { data: open } = useApi<Structure[]>("/trading/structures?status=open", { refreshInterval: 15_000 });

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center gap-2.5 border-b px-5">
        <div className="grid size-7 place-items-center rounded-lg bg-primary text-white">
          <CandlestickChart className="size-4" />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold">TradeBuddy</div>
          <div className="text-[10.5px] text-muted-foreground">Options desk</div>
        </div>
      </div>
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
        {NAV.map((group) => (
          <div key={group.group}>
            <div className="mb-1.5 px-3 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">{group.group}</div>
            {group.items.map((item) => {
              const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-900",
                    active && "bg-accent text-accent-foreground hover:bg-accent hover:text-accent-foreground",
                  )}
                >
                  <item.icon className={cn("size-4 shrink-0", active ? "text-primary" : "text-slate-400")} />
                  <span className="truncate">{item.label}</span>
                  {item.href === "/positions" && !!open?.length && (
                    <span className="num ml-auto rounded bg-slate-100 px-1.5 text-[10.5px] font-semibold text-slate-600">{open.length}</span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="flex items-center gap-2 border-t px-5 py-3 text-[11.5px] text-muted-foreground">
        <span className={cn("size-2 rounded-full", connected ? "bg-profit shadow-[0_0_0_3px_rgba(14,138,62,0.15)]" : "bg-slate-300")} />
        {connected ? "Live connection" : "Connecting…"}
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
