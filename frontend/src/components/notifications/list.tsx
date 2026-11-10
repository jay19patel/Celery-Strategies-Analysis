"use client";

import { AlertTriangle, CircleCheck, CircleX, OctagonX, Play, Scale, ShieldAlert, TrendingDown, TrendingUp, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { ago, arrowSigned, dateTime, pnlClass, titleCase, usd } from "@/lib/format";
import type { AppNotification } from "@/lib/types";
import { cn } from "@/lib/utils";

const KIND: Record<string, { title: string; icon: LucideIcon; tone: string }> = {
  StructureOpened: { title: "Position opened", icon: CircleCheck, tone: "bg-accent text-accent-foreground" },
  StructureClosed: { title: "Position closed", icon: TrendingUp, tone: "bg-profit-soft text-profit" },
  StructureFailed: { title: "Not opened", icon: CircleX, tone: "bg-warn-soft text-warn" },
  StructureError: { title: "Needs attention", icon: AlertTriangle, tone: "bg-loss-soft text-loss" },
  RiskHalt: { title: "Daily loss limit hit", icon: ShieldAlert, tone: "bg-loss-soft text-loss" },
  RiskResumed: { title: "Trading resumed", icon: Play, tone: "bg-profit-soft text-profit" },
  KillSwitch: { title: "Every entry stopped", icon: OctagonX, tone: "bg-loss-soft text-loss" },
  KillSwitchCleared: { title: "Entries resumed", icon: Play, tone: "bg-profit-soft text-profit" },
  ReconcileMismatch: { title: "Delta does not match the desk", icon: Scale, tone: "bg-loss-soft text-loss" },
  ReconcileMatched: { title: "Delta matches the desk again", icon: Scale, tone: "bg-profit-soft text-profit" },
};

function kind(n: AppNotification) {
  const k = KIND[n.type] ?? { title: n.type, icon: AlertTriangle, tone: "bg-secondary text-muted-foreground" };
  const pnl = typeof n.data?.pnl === "number" ? n.data.pnl : null;
  if (n.type === "StructureClosed" && pnl !== null) {
    return pnl < 0
      ? { title: `Position closed ${arrowSigned(pnl)}`, icon: TrendingDown, tone: "bg-loss-soft text-loss" }
      : { ...k, title: `Position closed ${arrowSigned(pnl)}` };
  }
  return k;
}

/** Marks notifications read (none given: all of them) and refreshes every list and the bell. */
export function useMarkRead() {
  const { mutate } = useSWRConfig();
  return async (ids: number[] = []) => {
    try {
      await api("/notifications/read", "POST", { ids });
      await mutate((key) => typeof key === "string" && key.startsWith("/notifications"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
}

// The event's own fields, labelled and formatted; anything else is shown as it came.
const FIELDS: Record<string, { label: string; show: (v: unknown) => string; money?: boolean }> = {
  pnl: { label: "P&L", show: (v) => arrowSigned(v as number), money: true },
  broker: { label: "Broker", show: String },
  underlying: { label: "Underlying", show: String },
  strategy: { label: "Strategy", show: (v) => titleCase(String(v)) },
  source: { label: "Opened by", show: (v) => (v === "auto" ? "Auto-trader" : titleCase(String(v))) },
  qty: { label: "Quantity", show: String },
  max_profit: { label: "Max profit", show: (v) => usd(v as number) },
  max_loss: { label: "Max loss", show: (v) => usd(v as number) },
  structure_id: { label: "Structure", show: String },
};
const LONG = new Set(["reason", "error"]); // a sentence, not a value: given the full width

function value(v: unknown): string {
  if (Array.isArray(v)) return v.length ? v.join("\n") : "none";
  if (v && typeof v === "object") return JSON.stringify(v);
  return typeof v === "number" ? String(Math.round(v * 1e6) / 1e6) : String(v);
}

function Detail({ n, onClose }: { n: AppNotification; onClose: () => void }) {
  const k = kind(n);
  const entries = Object.entries(n.data ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== "");
  const short = entries.filter(([key]) => !LONG.has(key));
  const long = entries.filter(([key]) => LONG.has(key));
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <div className="flex items-center gap-3">
            <span className={cn("grid size-10 shrink-0 place-items-center rounded-full", k.tone)}>
              <k.icon className="size-5" />
            </span>
            <div className="min-w-0">
              <DialogTitle>{k.title}</DialogTitle>
              <div className="num text-[12px] text-muted-foreground">
                {new Date(n.ts * 1000).toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" })} · {ago(n.ts)}
              </div>
            </div>
          </div>
        </DialogHeader>
        <DialogDescription className="whitespace-pre-wrap break-words text-[14px] text-foreground">{n.message}</DialogDescription>
        {long.map(([key, v]) => (
          <div key={key} className="rounded-xl bg-secondary/60 px-3.5 py-2.5">
            <div className="text-[11px] font-medium text-muted-foreground">{titleCase(key)}</div>
            <div className="mt-0.5 whitespace-pre-wrap break-words text-[13px]">{value(v)}</div>
          </div>
        ))}
        {short.length > 0 && (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border border-separator px-3.5 py-3">
            {short.map(([key, v]) => {
              const f = FIELDS[key];
              return (
                <div key={key} className={cn("min-w-0", key === "structure_id" || Array.isArray(v) ? "col-span-2" : "")}>
                  <dt className="text-[11px] font-medium text-muted-foreground">{f?.label ?? titleCase(key)}</dt>
                  <dd className={cn("num mt-0.5 whitespace-pre-wrap break-all text-[13px]", f?.money && pnlClass(v as number))}>{f ? f.show(v) : value(v)}</dd>
                </div>
              );
            })}
          </dl>
        )}
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <Badge variant={n.level === "error" ? "loss" : n.level === "warning" ? "warn" : "default"}>{n.level}</Badge>
          <span className="num">{n.type}</span>
          <span className="ml-auto">{n.read_at ? `read ${ago(n.read_at)}` : "unread"}</span>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function NotificationList({ rows, compact }: { rows: AppNotification[]; compact?: boolean }) {
  const markRead = useMarkRead();
  const [open, setOpen] = useState<AppNotification | null>(null);
  return (
    <>
      {open && <Detail n={rows.find((r) => r.id === open.id) ?? open} onClose={() => setOpen(null)} />}
      <ul className="divide-y divide-slate-100">
        {rows.map((n) => {
          const k = kind(n);
          const unread = n.read_at === null;
          return (
            <li key={n.id}>
              <button
                type="button"
                onClick={() => {
                  setOpen(n);
                  if (unread) markRead([n.id]);
                }}
                className={cn("flex w-full gap-3 px-4 py-3 text-left transition-colors", unread ? "bg-accent/30 hover:bg-accent/50" : "hover:bg-secondary/60")}
              >
                <span className={cn("grid size-8 shrink-0 place-items-center rounded-full", k.tone)}>
                  <k.icon className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className={cn("truncate text-[14px]", unread ? "font-semibold text-foreground" : "font-medium text-muted-foreground")}>{k.title}</span>
                    <span className="num shrink-0 text-[11px] text-muted-foreground" title={dateTime(n.ts)}>
                      {compact ? ago(n.ts) : dateTime(n.ts)}
                    </span>
                  </span>
                  <span className={cn("mt-0.5 block text-[12.5px] leading-snug text-muted-foreground", compact && "line-clamp-2")}>{n.message}</span>
                </span>
                {unread && <span className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" aria-label="unread" />}
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}
