"use client";

import { useMemo, useState } from "react";
import { ScrollText } from "lucide-react";

import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { dateTime } from "@/lib/format";
import { useApi, useLive } from "@/lib/live";
import type { EventRow } from "@/lib/types";
import { cn } from "@/lib/utils";

type Level = "all" | "warning";

const DOT: Record<string, string> = { info: "bg-slate-300", warning: "bg-amber-500", error: "bg-loss" };

export function EventLog() {
  const [level, setLevel] = useState<Level>("all");
  const { data, error, isLoading } = useApi<EventRow[]>(`/system/events?level=${level === "all" ? "" : "warning"}&limit=300`);
  const { events: live } = useLive();

  const rows = useMemo(() => {
    const stored = data ?? [];
    const newest = stored[0]?.ts ?? 0;
    const fresh = live
      .filter((e) => e.ts > newest && (level === "all" || e.level !== "info"))
      .map((e, i) => ({ id: -1 - i, ts: e.ts, type: e.type, level: e.level, message: e.message, data: e.data }));
    return [...fresh, ...stored];
  }, [data, live, level]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <ScrollText className="size-4 text-slate-400" /> Event log
        </CardTitle>
        <Tabs value={level} onValueChange={(v) => setLevel(v as Level)}>
          <TabsList className="h-8">
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="warning">Warnings + errors</TabsTrigger>
          </TabsList>
        </Tabs>
      </CardHeader>
      <CardContent className="p-0">
        {error && <LoadError error={error} className="m-4" />}
        {isLoading && !data && <Loading className="p-4" rows={6} />}
        {data && rows.length === 0 && <Empty title="No events" />}
        {rows.length > 0 && (
          <div className="max-h-[520px] divide-y divide-slate-100 overflow-y-auto">
            {rows.map((e) => (
              <div key={`${e.id}-${e.ts}`} className="flex items-start gap-3 px-4 py-2 text-[13px]">
                <span className="num w-[108px] shrink-0 pt-0.5 text-[11.5px] text-muted-foreground">{dateTime(e.ts)}</span>
                <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", DOT[e.level] ?? DOT.info)} />
                <Badge variant={e.level === "error" ? "loss" : e.level === "warning" ? "warn" : "default"} className="shrink-0">
                  {e.type}
                </Badge>
                <span className="min-w-0 break-words text-slate-700">{e.message}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
