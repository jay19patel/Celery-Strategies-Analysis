"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Pnl } from "@/components/shared/money";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { time } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Decision, JournalDay } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACTION_TONE = { open: "profit", skip: "default", error: "loss" } as const;
const LEVEL_DOT = { info: "bg-slate-300", warning: "bg-amber-500", error: "bg-red-500" } as const;

function DecisionRow({ d }: { d: Decision }) {
  const [open, setOpen] = useState(false);
  const candidates = d.candidates ?? [];
  return (
    <div className="border-b border-slate-100 py-2 last:border-0">
      <button type="button" className="flex w-full items-start gap-2 text-left" onClick={() => setOpen(!open)} disabled={!candidates.length}>
        {candidates.length ? (open ? <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-slate-400" /> : <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400" />) : <span className="w-3.5 shrink-0" />}
        <span className="num w-14 shrink-0 text-xs text-muted-foreground">{time(d.ts)}</span>
        <Badge variant={ACTION_TONE[d.action]}>{d.action}</Badge>
        <span className="min-w-0 text-[13px]">
          <span className="font-medium">{d.underlying}{d.strategy ? ` · ${d.strategy}` : ""}</span>{" "}
          <span className="text-muted-foreground">{d.reason}</span>
        </span>
      </button>
      {open && (
        <div className="ml-[5.5rem] mt-1.5 space-y-1">
          {candidates.map((c) => (
            <div key={c.strategy} className="flex items-baseline gap-2 text-xs">
              <span className="font-medium">{c.label}</span>
              <span className="num text-muted-foreground">score {c.score}</span>
              <span className={cn(c.status === "candidate" ? "text-profit" : "text-muted-foreground")}>{c.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Everything that happened on one trading day. */
export function DayPanel({ date }: { date: string }) {
  const { data, error, isLoading } = useApi<JournalDay>(`/portfolio/journal/day?date=${date}`);
  return (
    <div className="space-y-5">
      <LoadError error={error} />
      {isLoading && !data && <Loading rows={4} />}
      {data && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>
                {new Date(`${date}T00:00:00`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}
              </CardTitle>
              <span className="text-[13px]">Realised <Pnl value={data.pnl} /></span>
            </CardHeader>
            <CardContent className="py-2">
              {data.structures.length ? (
                data.structures.map((s) => (
                  <div key={s.id} className="flex flex-wrap items-center gap-2 border-b border-slate-100 py-2 text-[13px] last:border-0">
                    <Badge variant="primary">{s.broker}</Badge>
                    <span className="font-medium">{s.label}</span>
                    <span className="text-muted-foreground">{s.underlying} · {s.expiry_label} ×{s.qty}</span>
                    <Badge variant={s.status === "open" ? "warn" : s.status === "error" ? "loss" : "default"}>{s.status}</Badge>
                    <span className="ml-auto"><Pnl value={s.status === "closed" ? s.pnl : (s.live?.pnl ?? null)} /></span>
                  </div>
                ))
              ) : (
                <Empty title="No structures opened or closed this day" className="py-6" />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Auto decisions</CardTitle>
              <span className="num text-xs text-muted-foreground">{data.decisions.length}</span>
            </CardHeader>
            <CardContent className="py-1">
              {data.decisions.length ? data.decisions.map((d) => <DecisionRow key={d.id} d={d} />) : <Empty title="No auto decisions" className="py-6" />}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Event log</CardTitle>
              <span className="num text-xs text-muted-foreground">{data.events.length}</span>
            </CardHeader>
            <CardContent className="max-h-96 overflow-y-auto py-1">
              {data.events.length ? (
                data.events.map((e) => (
                  <div key={e.id} className="flex items-start gap-2 border-b border-slate-100 py-1.5 text-xs last:border-0">
                    <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", LEVEL_DOT[e.level])} />
                    <span className="num w-14 shrink-0 text-muted-foreground">{time(e.ts)}</span>
                    <span className="w-32 shrink-0 truncate font-medium">{e.type}</span>
                    <span className="min-w-0 text-slate-600">{e.message}</span>
                  </div>
                ))
              ) : (
                <Empty title="No events" className="py-6" />
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
