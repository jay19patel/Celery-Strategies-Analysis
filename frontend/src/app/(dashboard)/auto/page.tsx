"use client";

import { useState } from "react";
import Link from "next/link";
import { Bot, ListTree, Loader2, Play, Settings2 } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { DecisionRow } from "@/components/auto/decision-row";
import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError, Loading, Notice } from "@/components/shared/states";
import { KV } from "@/components/shared/stat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { ago, titleCase, until } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { AutoStatus, BrokerStatus, Decision } from "@/lib/types";

const CONFIG_ROWS: [string, string, (v: unknown) => string][] = [
  ["expiry_min_hours", "Expiry window", () => ""],
  ["short_delta", "Short strikes at |Δ|", (v) => String(v)],
  ["wing_width_pct", "Wings", (v) => `${v}% of spot`],
  ["min_score", "Minimum regime score", (v) => String(v)],
  ["min_pop_pct", "Minimum POP", (v) => `${v}%`],
  ["max_spread_pct", "Max bid/ask spread", (v) => `${v}%`],
  ["risk_per_trade_pct", "Risk per trade", (v) => `${v}% of equity`],
  ["max_open_structures", "Max open per broker", (v) => String(v)],
  ["credit_take_profit_pct", "Credit take profit", (v) => `${v}% of max profit`],
  ["credit_stop_loss_pct", "Credit stop loss", (v) => `${v}% of credit`],
  ["exit_before_expiry_minutes", "Exit before expiry", (v) => `${v} min`],
];

function BrokerSwitch({ b, onToggle }: { b: BrokerStatus; onToggle: (b: BrokerStatus, on: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border bg-card px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[13.5px] font-semibold">
          {b.name === "paper" ? "Paper" : "Delta"}
          {b.name === "delta" && <Badge variant={b.real_money ? "solid" : "primary"}>{b.real_money ? "REAL MONEY" : b.env}</Badge>}
          {b.halted && <Badge variant="loss">halted</Badge>}
        </div>
        <div className="truncate text-[11.5px] text-muted-foreground">
          {!b.active ? "broker not active (Settings)" : b.not_ready || b.halted || (b.auto ? "trading automatically" : "manual only")}
        </div>
      </div>
      <Switch checked={b.auto} disabled={!b.active} onCheckedChange={(on) => onToggle(b, on)} aria-label={`Auto-trade on ${b.name}`} />
    </div>
  );
}

export default function AutoPage() {
  const { mutate } = useSWRConfig();
  const [action, setAction] = useState("");
  const [running, setRunning] = useState(false);
  const [confirmDelta, setConfirmDelta] = useState<BrokerStatus | null>(null);
  const { data: status, error } = useApi<AutoStatus>("/auto/status", { refreshInterval: 15_000 });
  const { data: decisions, isLoading } = useApi<Decision[]>(`/auto/decisions?limit=100&action=${action}`, { refreshInterval: 30_000 });

  const toggle = async (b: BrokerStatus, on: boolean) => {
    if (b.name === "delta" && on && !confirmDelta) {
      setConfirmDelta(b);
      return;
    }
    try {
      await api("/auto/toggle", "POST", { broker: b.name, enabled: on });
      await mutate((k) => typeof k === "string" && (k.startsWith("/auto") || k.startsWith("/portfolio")));
      toast.success(`Auto-trading ${on ? "on" : "off"} for ${b.name}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setConfirmDelta(null);
    }
  };

  const runNow = async () => {
    setRunning(true);
    try {
      const r = await api<{ decisions: Decision[]; note?: string }>("/auto/run", "POST");
      if (r.note) toast.info(r.note);
      for (const d of r.decisions) (d.action === "open" ? toast.success : toast.info)(`${d.underlying}: ${d.action}`, { description: d.reason });
      await mutate((k) => typeof k === "string" && k.startsWith("/auto"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const cfg = status?.config;
  return (
    <>
      <PageHeader
        title="Auto Trader"
        description="Every cycle reads the regime, builds every enabled structure, keeps those that pass every check, optionally asks the AI reviewer, and sizes by risk."
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link href="/settings"><Settings2 /> Configure</Link>
            </Button>
            <Button size="sm" onClick={runNow} disabled={running}>
              {running ? <Loader2 className="animate-spin" /> : <Play />} Run a cycle now
            </Button>
          </>
        }
      />
      <LoadError error={error} />
      {status && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              {status.brokers.map((b) => (
                <BrokerSwitch key={b.name} b={b} onToggle={toggle} />
              ))}
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <Card className="px-4 py-3">
                <div className="text-[11.5px] text-muted-foreground">Next cycle</div>
                <div className="num mt-1 text-[15px] font-semibold">{until(status.job?.next_run)}</div>
                <div className="text-[11px] text-muted-foreground">every {cfg?.auto_interval_minutes} min · last {ago(status.job?.last_run)}</div>
              </Card>
              <Card className="px-4 py-3">
                <div className="text-[11.5px] text-muted-foreground">Strategies</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {cfg?.auto_strategies.map((s) => <Badge key={s} variant="outline">{titleCase(s)}</Badge>)}
                </div>
              </Card>
              <Card className="px-4 py-3">
                <div className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground"><Bot className="size-3.5" /> AI review</div>
                <div className="mt-1 text-[13px] font-semibold">
                  {!cfg?.ai_enabled ? "off — rules decide" : !cfg.ai_review_trades ? "brief only" : status.ai_ready ? "configured, not ready" : cfg.ai_required ? "required" : "on, rules as fallback"}
                </div>
                {status.ai_ready && cfg?.ai_enabled ? <div className="text-[11px] text-warn">{status.ai_ready}</div> : null}
              </Card>
            </div>
            {status.job?.last_error && <Notice tone="loss">Last cycle failed: {status.job.last_error}</Notice>}
          </div>
          <Card>
            <CardHeader>
              <CardTitle><ListTree className="size-4 text-slate-400" /> Rules in force</CardTitle>
            </CardHeader>
            <CardContent className="py-2">
              {cfg && (
                <>
                  <KV label="Expiry window" value={`${cfg.expiry_min_hours}–${cfg.expiry_max_hours}h`} />
                  {CONFIG_ROWS.slice(1).map(([key, label, fmt]) => (
                    <KV key={key} label={label} value={fmt(cfg[key])} />
                  ))}
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Decisions</CardTitle>
          <Tabs value={action || "all"} onValueChange={(v) => setAction(v === "all" ? "" : v)}>
            <TabsList>
              <TabsTrigger value="all">All</TabsTrigger>
              <TabsTrigger value="open">Opened</TabsTrigger>
              <TabsTrigger value="skip">Skipped</TabsTrigger>
              <TabsTrigger value="error">Errors</TabsTrigger>
            </TabsList>
          </Tabs>
        </CardHeader>
        {isLoading && !decisions && <Loading rows={5} className="p-4" />}
        {decisions?.length === 0 && <Empty title="No decisions yet">The first cycle runs on schedule, or run one now.</Empty>}
        <div>{decisions?.map((d) => <DecisionRow key={d.id} d={d} />)}</div>
      </Card>

      <Dialog open={!!confirmDelta} onOpenChange={(o) => !o && setConfirmDelta(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Auto-trade on Delta?</DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-2">
                <p>The engine will open and close option structures on your Delta {confirmDelta?.real_money ? "LIVE account with real money" : "demo account"} without asking.</p>
                <p>It switches itself off again whenever the engine restarts or the Delta account or keys change.</p>
              </div>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelta(null)}>Cancel</Button>
            <Button variant={confirmDelta?.real_money ? "destructive" : "default"} onClick={() => confirmDelta && toggle(confirmDelta, true)}>
              Switch on
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
