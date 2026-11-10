"use client";

// The first screen, built to answer five questions at a glance: is it running, what did today
// make, how far is each account from its peak, does anything need me, and what is open and why.

import Link from "next/link";
import { AlertTriangle, CheckCircle2 } from "lucide-react";

import { Pnl } from "@/components/shared/money";
import { PageHeader, SectionLabel } from "@/components/shared/page";
import { Stat } from "@/components/shared/stat";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ago, pnlClass, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Account, Accounts, Broker, StrategiesResponse, Structure, SystemStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const isAccount = (a: Account | { error: string } | undefined): a is Account => !!a && "equity" in a;

/** Drawdown from the highest equity a broker has stood at: the start of every recorded day, and now. */
function drawdown(equity: number, history: { broker: Broker; equity: number }[], broker: Broker): number | null {
  const peak = Math.max(equity, ...history.filter((h) => h.broker === broker).map((h) => h.equity));
  return peak > 0 ? (100 * (peak - equity)) / peak : null;
}

export default function OverviewPage() {
  const status = useApi<SystemStatus>("/system/status", { refreshInterval: 10_000 });
  const accounts = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 10_000 });
  const equity = useApi<{ broker: Broker; day: string; equity: number }[]>("/portfolio/equity?days=365");
  const open = useApi<Structure[]>("/trading/structures?status=open", { refreshInterval: 10_000 });
  const strategies = useApi<StrategiesResponse>("/strategies");

  const s = status.data;
  const failing = (s?.jobs ?? []).filter((j) => j.last_error);
  const attention: { key: string; text: string; href: string }[] = [
    ...(s?.stopped ? [{ key: "stopped", text: `Every entry is stopped: ${s.stopped.reason}`, href: "/" }] : []),
    ...(s && !s.feed.websocket.connected ? [{ key: "feed", text: `Live market feed is down: ${s.feed.websocket.error || "reconnecting"}`, href: "/system" }] : []),
    ...(s?.brokers ?? []).filter((b) => b.active && b.halted).map((b) => ({ key: `halt-${b.name}`, text: `${b.name} is halted for the day: ${b.halted}`, href: "/positions" })),
    ...(s?.brokers ?? []).filter((b) => b.active && b.reconcile).map((b) => ({ key: `rec-${b.name}`, text: `${b.name} does not match the exchange: ${b.reconcile}`, href: "/positions" })),
    ...(s?.brokers ?? []).filter((b) => b.active && !b.ready).map((b) => ({ key: `ready-${b.name}`, text: `${b.name} cannot trade: ${b.not_ready}`, href: "/settings" })),
    ...(open.data ?? []).filter((st) => st.status === "error").map((st) => ({ key: st.id, text: `${st.label} on ${st.broker}: ${st.error}`, href: "/positions" })),
    ...failing.map((j) => ({ key: j.id, text: `${j.label} failed ${ago(j.last_run)}: ${j.last_error}`, href: "/system" })),
  ];
  const running = s && !s.stopped && s.feed.websocket.connected && failing.length === 0;
  const on = (strategies.data?.strategies ?? []).filter((x) => x.config.enabled);

  return (
    <div className="space-y-6">
      <PageHeader title="Overview" description="Is it running, what did today make, and does anything need you." />
      <LoadError error={status.error ?? accounts.error} />
      {!s && status.isLoading && <Loading rows={4} />}

      {s && (
        <Card className={cn(!running && "border-loss")}>
          <CardContent className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3 text-[13px]">
            <span className={cn("size-2.5 shrink-0 rounded-full", running ? "bg-profit" : "bg-loss")} />
            <span className="font-semibold">{s.stopped ? "Stopped" : running ? "Running" : "Running, with problems"}</span>
            <span className="text-muted-foreground">up {Math.round(s.uptime_seconds / 3600)}h</span>
            <span className="text-muted-foreground">{on.length} strategies on</span>
            <span className="text-muted-foreground">
              automatic on {s.brokers.filter((b) => b.active && b.auto).map((b) => b.name).join(" and ") || "no broker"}
            </span>
          </CardContent>
        </Card>
      )}

      <SectionLabel title="Accounts" hint="each broker on its own, never added together" />
      <div className="grid gap-4 md:grid-cols-2">
        {(accounts.data?.brokers ?? []).filter((b) => b.active).map((b) => {
          const acct = accounts.data?.accounts[b.name];
          const today = accounts.data?.today[b.name];
          if (!isAccount(acct)) {
            return (
              <Card key={b.name}>
                <CardContent className="py-4 text-[13px] text-loss">{b.name}: {acct?.error ?? "no account"}</CardContent>
              </Card>
            );
          }
          const day = today?.start_equity ? acct.equity - today.start_equity : null;
          const dd = drawdown(acct.equity, equity.data ?? [], b.name);
          return (
            <Card key={b.name}>
              <CardContent className="space-y-3 py-4">
                <div className="flex items-center gap-2">
                  <span className="text-[15px] font-semibold capitalize">{b.name}</span>
                  <Badge variant={b.real_money ? "solid" : "outline"}>{b.real_money ? "real money" : b.name === "delta" ? "demo" : "paper"}</Badge>
                  {b.halted && <Badge variant="loss">halted</Badge>}
                </div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Stat label="Equity" value={usd(acct.equity)} className="bg-secondary/60" />
                  <Stat label="Today" value={<Pnl value={day} />} hint={today?.start_equity ? `from ${usd(today.start_equity)}` : undefined} className="bg-secondary/60" />
                  <Stat
                    label="From peak"
                    value={dd === null ? "—" : `${dd <= 0.005 ? "0.0" : `−${dd.toFixed(1)}`}%`}
                    valueClass={pnlClass(dd ? -dd : 0)}
                    className="bg-secondary/60"
                  />
                  <Stat label="Free margin" value={usd(acct.available)} className="bg-secondary/60" />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <SectionLabel title="Needs attention" hint={attention.length ? `${attention.length}` : undefined} />
      <Card>
        <CardContent className="space-y-1.5 py-3">
          {attention.length === 0 ? (
            <div className="flex items-center gap-2 text-[13px] text-profit">
              <CheckCircle2 className="size-4" /> Nothing needs you.
            </div>
          ) : (
            attention.map((a) => (
              <Link key={a.key} href={a.href} className="flex items-start gap-2 rounded-md px-1 py-1 text-[13px] hover:bg-muted/50">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-loss" />
                <span>{a.text}</span>
              </Link>
            ))
          )}
        </CardContent>
      </Card>

      <SectionLabel title="Open positions" hint={open.data ? `${open.data.length}` : undefined} live={(open.data?.length ?? 0) > 0} />
      <Card>
        <CardContent className="p-0">
          <LoadError error={open.error} />
          {open.data && open.data.length === 0 && <Empty title="Nothing open">Strategies switched on open positions here when their rules fire.</Empty>}
          {(open.data ?? []).map((st) => (
            <Link key={st.id} href="/positions" className="flex flex-col gap-1 border-b border-separator px-4 py-3 last:border-0 hover:bg-muted/40 sm:flex-row sm:items-start sm:gap-4">
              <div className="flex shrink-0 items-center gap-2 sm:w-64">
                <span className="text-[13px] font-semibold">{st.label}</span>
                <span className="num text-[12px] text-muted-foreground">{st.underlying}</span>
                <Badge variant="outline" className="capitalize">{st.broker}</Badge>
              </div>
              <span className="min-w-0 flex-1 text-[12.5px] text-muted-foreground">{st.reason || (st.source === "manual" ? "opened from the dashboard" : "")}</span>
              <span className="shrink-0 text-right">
                <Pnl value={st.live?.pnl ?? st.mark_pnl} />
              </span>
            </Link>
          ))}
        </CardContent>
      </Card>

      <SectionLabel title="Strategies on" />
      <Card>
        <CardContent className="space-y-1 py-3 text-[13px]">
          {on.length === 0 && <span className="text-muted-foreground">None. Switch one on in <Link href="/strategies" className="text-primary hover:underline">Strategies</Link>.</span>}
          {on.map((x) => (
            <div key={x.name} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{x.label}</span>
              <Badge variant={x.config.live ? "solid" : "outline"}>{x.config.live ? "paper + live" : "paper"}</Badge>
              <span className="text-muted-foreground">
                {x.record.trades_r} trades
                {x.record.expectancy_r != null && `, ${x.record.expectancy_r.toFixed(2)}R average`}
              </span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
