"use client";

import { useState } from "react";

import { NotificationList, useMarkRead } from "@/components/notifications/list";
import { PageHeader } from "@/components/shared/page";
import { Pager } from "@/components/shared/pager";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApi } from "@/lib/live";
import type { NotificationPage } from "@/lib/types";

const PAGE_SIZE = 20;

export default function NotificationsPage() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [page, setPage] = useState(1);
  const markRead = useMarkRead();
  const { data, error, isLoading } = useApi<NotificationPage>(`/notifications?page=${page}&size=${PAGE_SIZE}&unread=${unreadOnly}`);

  return (
    <>
      <PageHeader
        title="Notifications"
        description="Every position opened or closed, every failure, and every risk halt — kept for 30 days. Tap one to read it in full."
        actions={
          <>
            <Tabs
              value={unreadOnly ? "unread" : "all"}
              onValueChange={(v) => {
                setUnreadOnly(v === "unread");
                setPage(1);
              }}
            >
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="unread">Unread{data?.unread ? ` (${data.unread})` : ""}</TabsTrigger>
              </TabsList>
            </Tabs>
            <Button variant="secondary" size="sm" disabled={!data?.unread} onClick={() => markRead()}>
              Mark all read
            </Button>
          </>
        }
      />
      <LoadError error={error} />
      <Card className="overflow-hidden">
        {isLoading && !data ? (
          <Loading className="p-4" rows={6} />
        ) : !data?.rows.length ? (
          <Empty title={unreadOnly ? "Nothing unread" : "No notifications"}>Positions opened or closed, and risk halts, appear here.</Empty>
        ) : (
          <>
            <NotificationList rows={data.rows} />
            <Pager data={data} label="notifications" onPage={setPage} />
          </>
        )}
      </Card>
    </>
  );
}
