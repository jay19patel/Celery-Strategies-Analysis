"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Lock, OctagonX } from "lucide-react";
import { toast } from "sonner";

import { ALL_NAV, isActive } from "@/components/layout/nav";
import { NotificationsBell } from "@/components/layout/notifications-bell";
import { Confirm } from "@/components/shared/confirm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useApi, useLive } from "@/lib/live";
import type { Accounts } from "@/lib/types";
import { cn } from "@/lib/utils";

/** True while the page's large title (`data-large-title`) is on screen; the bar then leaves the title to it. */
function useLargeTitleInView(pathname: string): boolean {
  const [seen, setSeen] = useState<{ path: string; inView: boolean } | null>(null);
  useEffect(() => {
    let io: IntersectionObserver | undefined;
    const attach = () => {
      const el = document.querySelector("[data-large-title]");
      if (!el) return false;
      io = new IntersectionObserver(([e]) => setSeen({ path: pathname, inView: e.isIntersecting }), { rootMargin: "-90px 0px 0px 0px" });
      io.observe(el);
      return true;
    };
    // The title can render after the page's data arrives, so keep looking until it does.
    const mo = new MutationObserver(() => attach() && mo.disconnect());
    if (!attach()) mo.observe(document.body, { childList: true, subtree: true });
    return () => {
      io?.disconnect();
      mo.disconnect();
    };
  }, [pathname]);
  return seen?.path === pathname && seen.inView;
}

export function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const { connected } = useLive();
  const largeTitleInView = useLargeTitleInView(pathname);

  const { data: accounts } = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 20_000 });
  const title = ALL_NAV.find((n) => isActive(pathname, n.href))?.label ?? "TradeBuddy";
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
    <header className={cn("material sticky top-(--ticker-h) z-30 transition-[border-color] duration-200", largeTitleInView ? "border-b border-transparent" : "border-b border-separator")}>
      <div className="flex h-12 items-center gap-2 px-4 sm:gap-3 lg:h-14 lg:px-6">
        <div className={cn("flex min-w-0 items-center gap-2 transition-opacity duration-200", largeTitleInView ? "opacity-0" : "opacity-100")}>
          <div className="truncate text-[17px] font-semibold tracking-[-0.02em]">{title}</div>
          <span
            className={cn("size-2 shrink-0 rounded-full lg:hidden", connected ? "bg-profit" : "bg-slate-300")}
            title={connected ? "Live" : "Connecting…"}
          />
        </div>

        <div className="flex-1" />

        {realMoney && (
          <Badge variant="solid" className="px-2 text-[11px] tracking-wide">
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
            <Button variant="destructive-outline" size="sm" onClick={open} className="h-8 rounded-full px-3" aria-label="Close all">
              <OctagonX className="size-4" /> <span className="hidden sm:inline">Close all</span>
            </Button>
          )}
        />

        <NotificationsBell />

        <Button variant="outline" size="icon-sm" onClick={lock} title="Lock the dashboard" className="rounded-full" aria-label="Lock">
          <Lock className="size-4" />
        </Button>
      </div>
    </header>
  );
}
