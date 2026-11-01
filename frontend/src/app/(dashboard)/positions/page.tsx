"use client";

import { useState } from "react";
import Link from "next/link";
import { Layers } from "lucide-react";
import { toast } from "sonner";

import { sumCosts } from "@/components/positions/derive";
import { StructureCard } from "@/components/positions/structure-card";
import { BrokerTabs, type BrokerFilter } from "@/components/shared/broker-tabs";
import { Confirm } from "@/components/shared/confirm";
import { Pnl } from "@/components/shared/money";
import { PageHeader, SectionLabel } from "@/components/shared/page";
import { Empty, LoadError, Loading, Notice } from "@/components/shared/states";
import { Stat } from "@/components/shared/stat";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import { num, pnlClass, signed, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Account, Accounts, Broker, BrokerStatus, SettingsResponse, Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

function AccountRow({
  name,
  account,
  today,
  open,
  status,
  limitPct,
  onResumed,
}: {
  name: Broker;
  account: Account | { error: string } | undefined;
  today: { day: string; start_equity: number | null; halted: string } | undefined;
  open: Structure[];
  status: BrokerStatus | undefined;
  limitPct: number;
  onResumed: () => void;
}) {
  if (!account) return null;
  if ("error" in account) {
    return (
      <Card className="px-4 py-3 text-[13px]">
        <span className="font-semibold capitalize">{name}</span> <span className="text-loss">— {account.error}</span>
      </Card>
    );
  }
  const start = typeof account.extra.starting_balance === "number" ? account.extra.starting_balance : null;
  const usedPct = account.equity > 0 ? (100 * account.margin_used) / account.equity : null;
  const c = sumCosts(open);
  // The daily loss limit measures equity — open positions included — from the day's opening equity.
  const onDay = today?.start_equity != null ? account.equity - today.start_equity : null;
  const limitUsd = today?.start_equity != null && limitPct ? (today.start_equity * limitPct) / 100 : null;
  const used = limitUsd && onDay !== null ? Math.min(100, (100 * Math.max(0, -onDay)) / limitUsd) : 0;
  const label = name === "paper" ? "Paper" : "Delta";

  const resume = async () => {
    try {
      await api("/portfolio/resume", "POST", { broker: name });
      toast.success(`${name} resumed`);
      onResumed();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-3">
      {/* SectionLabel pulls itself towards what follows; this row needs its own breathing room. */}
      <div className="pb-1.5 pt-1">
        <SectionLabel
          title={`${label} · ${open.length} open`}
          live={open.length > 0}
          hint={open.length ? "unrealised: valued at what closing every leg now would book" : "nothing open"}
        />
      </div>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="Unrealised P&L"
          value={open.length ? signed(c.net, 4) : "—"}
          valueClass={pnlClass(c.net)}
          hint={open.length ? "net, if closed now" : "no open position"}
        />
        <Stat label="Gross move" value={open.length ? signed(c.gross, 4) : "—"} valueClass={pnlClass(c.gross)} hint="at the marks, before any fee" />
        <Stat label="Charges paid" value={usd(c.paid, 4)} valueClass={c.paid ? "text-warn" : undefined} hint="entry fees on open positions" />
        <Stat label="Exit charges" value={open.length ? usd(c.exit, 4) : "—"} valueClass={c.exit ? "text-warn" : undefined} hint="fees to close now (est.)" />
        <Stat label="Spread cost" value={open.length ? usd(c.spread, 4) : "—"} valueClass={c.spread ? "text-warn" : undefined} hint="bid/ask against the marks" />
        <Stat label="Margin in use" value={usd(account.margin_used)} hint={usedPct === null ? undefined : `${usedPct.toFixed(1)}% · ${usd(account.available)} free`} />
      </div>
      <Card className="space-y-2 px-4 py-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-[13px]">
          <span>
            <span className="text-muted-foreground">Equity </span>
            <span className="num font-semibold">{usd(account.equity)}</span>
            {start !== null && <span className="num text-muted-foreground"> · {signed(account.equity - start)} since start</span>}
          </span>
          <span>
            <span className="text-muted-foreground">Today (incl. open) </span>
            <Pnl value={onDay} />
            {today?.start_equity != null && <span className="num text-muted-foreground"> from {usd(today.start_equity)}</span>}
          </span>
        </div>
        {limitUsd !== null && (
          <div>
            <div className="mb-1 flex justify-between gap-2 text-[11px]">
              <span className="text-muted-foreground">Daily loss limit {num(limitPct, 2)}% · {usd(limitUsd)}</span>
              <span className={cn("num", used >= 80 ? "text-loss" : "text-muted-foreground")}>{num(used, 0)}% used</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className={cn("h-full rounded-full transition-[width] duration-300 ease-out", used >= 80 ? "bg-loss" : used >= 50 ? "bg-amber-500" : "bg-profit")}
                style={{ width: `${used}%` }}
              />
            </div>
          </div>
        )}
      </Card>
      {(today?.halted || status?.halted) && (
        <Notice tone="loss">
          <span className="flex flex-wrap items-center justify-between gap-2">
            <span>
              <span className="font-semibold capitalize">{name}</span> is halted for {today?.day}: {today?.halted || status?.halted}
            </span>
            <Confirm
              title={`Resume ${name} today?`}
              description="New entries are allowed again. Today's limit restarts from the current equity."
              confirmLabel="Resume trading"
              destructive
              onConfirm={resume}
              trigger={(o) => <Button variant="destructive-outline" size="xs" onClick={o}>Resume</Button>}
            />
          </span>
        </Notice>
      )}
    </div>
  );
}

export default function PositionsPage() {
  const [broker, setBroker] = useState<BrokerFilter>("");
  const { data: accounts, mutate } = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 3_000 });
  const { data: settings } = useApi<SettingsResponse>("/settings");
  const { data, error, isLoading } = useApi<Structure[]>(`/trading/structures?status=open&broker=${broker}`, { refreshInterval: 3_000 });
  const limitPct = Number(settings?.values.daily_loss_limit_pct ?? 0);

  return (
    <>
      <PageHeader
        title="Positions"
        description="Everything still open: unrealised P&L, the charges paid and still to pay, and the daily loss limit. Booked results are on Performance."
        actions={<BrokerTabs value={broker} onChange={setBroker} />}
      />
      <div className="space-y-5">
        {Object.entries(accounts?.accounts ?? {})
          .filter(([name]) => !broker || name === broker)
          .map(([name, account]) => (
            <AccountRow
              key={name}
              name={name as Broker}
              account={account}
              today={accounts?.today?.[name as Broker]}
              open={(data ?? []).filter((s) => s.broker === name)}
              status={accounts?.brokers.find((b) => b.name === name)}
              limitPct={limitPct}
              onResumed={() => mutate()}
            />
          ))}
      </div>
      <LoadError error={error} />
      {isLoading && !data && <Loading rows={4} />}
      {data && data.length === 0 && (
        <Card>
          <Empty title="No open structures">
            Open one from the <Link href="/" className="text-primary hover:underline">Options Desk</Link>, or let the <Link href="/auto" className="text-primary hover:underline">Auto Trader</Link> find one.
          </Empty>
          <div className="flex justify-center pb-6">
            <Button asChild variant="outline" size="sm"><Link href="/"><Layers /> Open the desk</Link></Button>
          </div>
        </Card>
      )}
      {!!data?.length && (
        <div className="pb-1.5 pt-3">
          <SectionLabel title="Open structures" hint="each one, leg by leg" />
        </div>
      )}
      <div className="space-y-4">
        {data?.map((s) => (
          <StructureCard key={s.id} s={s} />
        ))}
      </div>
    </>
  );
}
