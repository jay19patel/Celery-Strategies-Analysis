"use client";

import { useState } from "react";
import Link from "next/link";
import { Layers } from "lucide-react";

import { StructureCard } from "@/components/positions/structure-card";
import { BrokerTabs, type BrokerFilter } from "@/components/shared/broker-tabs";
import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError, Loading, Notice } from "@/components/shared/states";
import { Stat } from "@/components/shared/stat";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { signed, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Account, Accounts, Broker, Structure } from "@/lib/types";

function AccountRow({
  name,
  account,
  today,
}: {
  name: string;
  account: Account | { error: string } | undefined;
  today: { day: string; start_equity: number | null; halted: string } | undefined;
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
  // How much of the equity is committed, and how the day is going against the loss limit's baseline.
  const usedPct = account.equity > 0 ? (100 * account.margin_used) / account.equity : null;
  const onDay = today?.start_equity != null ? account.equity - today.start_equity : null;
  return (
    <div className="space-y-2.5">
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-5">
        <Stat label={`${name === "paper" ? "Paper" : "Delta"} equity`} value={usd(account.equity)} hint={start !== null ? `${signed(account.equity - start)} since start` : account.currency} />
        <Stat label="Available" value={usd(account.available)} hint="for new margin" />
        <Stat label="Margin in use" value={usd(account.margin_used)} hint={usedPct === null ? undefined : `${usedPct.toFixed(1)}% of equity`} />
        <Stat label="Cash" value={usd(account.cash)} hint={typeof account.extra.fees_paid === "number" ? `fees paid ${usd(account.extra.fees_paid)}` : undefined} />
        <Stat className="col-span-2 sm:col-span-1" label="Today" value={onDay === null ? "—" : signed(onDay)} hint={today?.start_equity != null ? `from ${usd(today.start_equity)} at open` : "no day recorded yet"} />
      </div>
      {today?.halted && (
        <Notice tone="loss">
          <span className="font-semibold capitalize">{name}</span> is halted for {today.day}: {today.halted}
        </Notice>
      )}
    </div>
  );
}

export default function PositionsPage() {
  const [broker, setBroker] = useState<BrokerFilter>("");
  const { data: accounts } = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 5_000 });
  const { data, error, isLoading } = useApi<Structure[]>(`/trading/structures?status=open&broker=${broker}`, { refreshInterval: 3_000 });

  return (
    <>
      <PageHeader title="Positions" description="Open structures, valued at what closing every leg now would book." actions={<BrokerTabs value={broker} onChange={setBroker} />} />
      <div className="space-y-2.5">
        {Object.entries(accounts?.accounts ?? {})
          .filter(([name]) => !broker || name === broker)
          .map(([name, account]) => (
            <AccountRow key={name} name={name} account={account} today={accounts?.today?.[name as Broker]} />
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
      <div className="space-y-4">
        {data?.map((s) => (
          <StructureCard key={s.id} s={s} />
        ))}
      </div>
    </>
  );
}
