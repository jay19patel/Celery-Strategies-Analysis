"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Layers, Menu, Radar, Sparkles, Zap } from "lucide-react";

import { useApi } from "@/lib/live";
import type { Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

interface BottomNavProps {
  onOpenMenu: () => void;
}

const PRIMARY_TABS = [
  { href: "/", label: "Desk", icon: Sparkles },
  { href: "/market", label: "Market", icon: Radar },
  { href: "/zero-hero", label: "Zero-Hero", icon: Zap },
  { href: "/positions", label: "Positions", icon: Layers, badge: true },
];

export function BottomNav({ onOpenMenu }: BottomNavProps) {
  const pathname = usePathname();
  const { data: open } = useApi<Structure[]>("/trading/structures?status=open", {
    refreshInterval: 15_000,
  });
  const openCount = open?.length ?? 0;

  return (
    <nav
      aria-label="Mobile Navigation"
      className="fixed bottom-0 left-0 right-0 z-40 block border-t border-slate-200/90 bg-white/95 pb-safe backdrop-blur-md lg:hidden shadow-[0_-3px_15px_rgba(15,23,42,0.06)]"
    >
      <div className="flex h-14 items-center justify-around px-2">
        {PRIMARY_TABS.map((tab) => {
          const active = tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href);
          const Icon = tab.icon;

          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={cn(
                "relative flex flex-1 flex-col items-center justify-center py-1 transition-all active:scale-95",
                active ? "text-primary font-semibold" : "text-slate-500 hover:text-slate-900",
              )}
            >
              <div className="relative">
                <Icon className={cn("size-5", active ? "text-primary stroke-[2.2]" : "text-slate-500 stroke-[1.8]")} />
                {tab.badge && openCount > 0 && (
                  <span className="absolute -top-1 -right-2 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[9.5px] font-bold text-white shadow-xs">
                    {openCount}
                  </span>
                )}
              </div>
              <span className="mt-0.5 text-[10.5px] tracking-tight">{tab.label}</span>
              {active && (
                <span className="absolute -bottom-1 h-0.5 w-6 rounded-full bg-primary" />
              )}
            </Link>
          );
        })}

        {/* Menu Tab for secondary items */}
        <button
          type="button"
          onClick={onOpenMenu}
          aria-label="Open More Menu"
          className="relative flex flex-1 flex-col items-center justify-center py-1 text-slate-500 transition-all active:scale-95 hover:text-slate-900"
        >
          <Menu className="size-5 stroke-[1.8]" />
          <span className="mt-0.5 text-[10.5px] tracking-tight">More</span>
        </button>
      </div>
    </nav>
  );
}
