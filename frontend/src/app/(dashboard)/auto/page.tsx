"use client";

import { useState } from "react";
import Link from "next/link";
import { ListTree, Loader2, Play, Settings2 } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { DecisionRow } from "@/components/auto/decision-row";
import { Confirm } from "@/components/shared/confirm";
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
import { ago, until } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { AutoStatus, BrokerStatus, Decision } from "@/lib/types";

function BrokerSwitch({ b, onToggle }: { b: BrokerStatus; onToggle: (b: BrokerStatus, on: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl bg-card px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[13.5px] font-semibold">
          {b.name === "paper" ? "Paper" : "Delta"}
          {b.name === "delta" && <Badge variant={b.real_money ? "solid" : "primary"}>{b.real_money ? "REAL MONEY" : b.env}</Badge>}
          {b.halted && <Badge variant="loss">halted</Badge>}
        </div>
        <div className="truncate text-[11.5px] text-muted-foreground">
          {!b.active ? "broker not active (Settings)" : b.not_ready || b.halted || b.reconcile || (b.auto ? "automatic entries on" : "manual only")}
        </div>
      </div>
      <Switch checked={b.auto} disabled={!b.active} onCheckedChange={(on) => onToggle(b, on)} aria-label={`Automatic entries on ${b.name}`} />
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
      toast.success(`Automatic entries ${on ? "on" : "off"} for ${b.name}`);
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
        title="Option Decisions"
        description="Every cycle, each option strategy switched on reads the regime at its own expiry, builds its structure, and must pass every check; the rules decide and size by risk. Every cycle that reached a broker is kept here, with why."
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link href="/strategies"><Settings2 /> Strategies</Link>
            </Button>
            <Confirm
              title="Run a cycle now?"
              description={`It decides and trades now on every broker with automatic entries on: ${status?.brokers.filter((b) => b.active && b.auto).map((b) => (b.real_money ? `${b.name} (real money)` : b.name)).join(", ") || "none"}.`}
              confirmLabel="Run it"
              destructive={status?.brokers.some((b) => b.active && b.auto && b.real_money)}
              onConfirm={runNow}
              trigger={(open) => (
                <Button size="sm" onClick={open} disabled={running}>
                  {running ? <Loader2 className="animate-spin" /> : <Play />} Run a cycle now
                </Button>
              )}
            />
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
            <div className="grid gap-3 sm:grid-cols-2">
              <Card className="px-4 py-3">
                <div className="text-[11.5px] text-muted-foreground">Next cycle</div>
                <div className="num mt-1 text-[15px] font-semibold">{until(status.job?.next_run)}</div>
                <div className="text-[11px] text-muted-foreground">every {cfg?.interval_minutes} min · last {ago(status.job?.last_run)}</div>
              </Card>
              <Card className="px-4 py-3">
                <div className="text-[11.5px] text-muted-foreground">Option strategies on</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {cfg?.strategies.length ? cfg.strategies.map((s) => <Badge key={s} variant="outline">{s.replace(/_/g, " ")}</Badge>) : <span className="text-[12px] text-muted-foreground">none</span>}
                </div>
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
                  <KV label="Score a strategy must reach" value={String(cfg.min_score)} />
                  <KV label="Risk per entry" value={`${cfg.risk_per_trade_pct}% of equity`} />
                  <KV label="Widest leg spread" value={`${cfg.max_spread_pct}%`} />
                  <KV label="Exit before expiry" value={`${cfg.exit_before_expiry_minutes} min`} />
                  <KV label="Each strategy's own numbers" value={<Link href="/strategies" className="text-primary hover:underline">Strategies</Link>} />
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {status && status.now.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Right now</CardTitle>
            <span className="text-[11.5px] text-muted-foreground">the latest cycle per underlying, skips included: why it did or did not trade (shown live, not stored)</span>
          </CardHeader>
          <div>{status.now.map((d) => <DecisionRow key={`${d.underlying}-${d.ts}`} d={d} />)}</div>
        </Card>
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
            <DialogTitle>Automatic entries on Delta?</DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-2">
                <p>Every strategy switched to live will open and close positions on your Delta {confirmDelta?.real_money ? "LIVE account with real money" : "demo account"} without asking.</p>
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
