"use client";

import Link from "next/link";
import { Bell } from "lucide-react";
import { useState } from "react";

import { NotificationList, useMarkRead } from "@/components/notifications/list";
import { Empty, Loading } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useApi } from "@/lib/live";
import type { NotificationPage } from "@/lib/types";

/** The header's bell: the unread count, and the latest notifications in a side panel. */
export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const markRead = useMarkRead();
  // Live events refresh it at once; the interval only covers a dropped socket.
  const { data } = useApi<NotificationPage>("/notifications?size=20", { refreshInterval: 60_000 });
  const unread = data?.unread ?? 0;

  return (
    <>
      <Button variant="outline" size="icon-sm" onClick={() => setOpen(true)} className="relative rounded-full" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}>
        <Bell className="size-4" />
        {unread > 0 && (
          <span className="num absolute -top-1 -right-1 grid h-4 min-w-4 place-items-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" aria-describedby={undefined}>
          <div className="flex items-center justify-between gap-2 border-b border-separator px-4 py-3">
            <div>
              <SheetTitle className="text-[18px] font-bold tracking-[-0.02em]">Notifications</SheetTitle>
              <div className="text-[12px] text-muted-foreground">{unread ? `${unread} unread` : "All caught up"}</div>
            </div>
            <Button variant="secondary" size="xs" className="rounded-full" disabled={!unread} onClick={() => markRead()}>
              Mark all read
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {!data ? (
              <Loading className="p-4" rows={5} />
            ) : !data.rows.length ? (
              <Empty title="No notifications">Positions opened or closed, and risk halts, appear here.</Empty>
            ) : (
              <NotificationList rows={data.rows} compact />
            )}
          </div>
          <div className="border-t border-separator p-3">
            <Button asChild variant="ghost" size="sm" className="w-full">
              <Link href="/notifications" onClick={() => setOpen(false)}>
                See all notifications
              </Link>
            </Button>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
