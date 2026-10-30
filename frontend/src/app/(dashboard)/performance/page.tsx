"use client";

import { useState } from "react";
import Link from "next/link";
import { LineChart, PieChart, RotateCcw, Trophy, Users } from "lucide-react";
import { toast } from "sonner";

import { AccountSection } from "@/components/performance/account-section";
import { duration } from "@/components/performance/derive";
import { EquityCurve } from "@/components/performance/equity-curve";
import { ExitReasons } from "@/components/performance/exit-reasons";
import { StrategySection } from "@/components/performance/strategy-section";
import { SummaryTable } from "@/components/performance/summary-table";
import { TradesTable } from "@/components/performance/trades-table";
import { Confirm } from "@/components/shared/confirm";
import { Stat } from "@/components/shared/stat";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { num, pct, pnlClass, signed, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Accounts, Broker, SettingsResponse, Stats, Structure } from "@/lib/types";

export default function PerformancePage() {
  const [broker, setBroker] = useState<Broker>("paper");
  const [strategy, setStrategy] = useState("");
  const accounts = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 5_000 });
  const settings = useApi<SettingsResponse>("/settings");
  const stats = useApi<Stats>(`/portfolio/stats?broker=${broker}`, { refreshInterval: 15_000 });
  const closed = useApi<Structure[]>(`/trading/structures?status=closed&broker=${broker}&limit=1000`, { refreshInterval: 15_000 });
  const open = useApi<Structure[]>(`/trading/structures?status=open&broker=${broker}`, { refreshInterval: 5_000 });

  const account = accounts.data?.accounts[broker];
  const real = account && !("error" in account) ? account : undefined;
  const start = broker === "paper" && real && typeof real.extra.starting_balance === "number" ? real.extra.starting_balance : 0;
  const opened = real && typeof real.extra.created_at === "number" ? real.extra.created_at : null;
  const o = stats.data?.overall;
  const rows = closed.data ?? [];

  const reset = async () => {
    try {
      await api("/portfolio/paper/reset", "POST");
      toast.success("Paper account reset");
      await Promise.all([stats.mutate(), closed.mutate(), accounts.mutate()]);
    } catch (e) {
      toast.error("Not reset", { description: e instanceof Error ? e.message : String(e) });
      throw e;
    }
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={broker} onValueChange={(v) => { setBroker(v as Broker); setStrategy(""); }}>
          <TabsList>
            <TabsTrigger value="paper">Paper</TabsTrigger>
            <TabsTrigger value="delta">Delta</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <LoadError error={accounts.error} />
      {!accounts.data ? (
        <Loading rows={2} />
      ) : !account ? (
        <Empty title={`${broker === "paper" ? "Paper" : "Delta"} is not active`}>
          Switch it on in <Link href="/settings" className="text-primary hover:underline">Settings</Link>.
        </Empty>
      ) : (
        <AccountSection
          broker={broker}
          account={account}
          status={accounts.data.brokers.find((b) => b.name === broker)}
          open={open.data ?? []}
          stats={stats.data}
          settings={settings.data?.values}
          onResumed={() => accounts.mutate()}
        />
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
        <p className="max-w-3xl text-xs text-slate-500">
          {broker === "paper"
            ? "The paper broker fills each leg at the live bid or ask (the mark plus slippage only when that side is empty), charges the fee from Delta's product spec, and settles anything still open at expiry on the index. Configure it in "
            : "Results of every structure closed on Delta. Configure the account in "}
          <Link className="underline" href="/settings">Settings</Link>.
        </p>
        {broker === "paper" && (
          <Confirm
            title="Reset the paper account?"
            description="The whole paper trade history is deleted and the balance goes back to the starting balance from Settings. Refused while a paper structure is open."
            confirmLabel="Reset"
            destructive
            onConfirm={reset}
            trigger={(openDialog) => (
              <Button variant="destructive-outline" size="sm" onClick={openDialog}>
                <RotateCcw /> Reset paper account
              </Button>
            )}
          />
        )}
      </div>

      <LoadError error={stats.error} />
      {!o ? (
        <Loading rows={2} />
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5 xl:grid-cols-10">
          <Stat
            label="Gross P&L"
            value={o.gross_pnl !== undefined ? signed(o.gross_pnl, 4) : signed(o.pnl + o.fees, 4)}
            valueClass={pnlClass(o.gross_pnl !== undefined ? o.gross_pnl : o.pnl + o.fees)}
            hint="before fees"
          />
          <Stat
            label="Total Charges"
            value={usd(o.fees, 4)}
            valueClass="text-amber-600 dark:text-amber-400"
            hint="broker & exchange"
          />
          <Stat label="Net realised" value={signed(o.pnl, 4)} valueClass={pnlClass(o.pnl)} hint="after all fees" />
          <Stat label="Trades" value={num(o.trades, 0)} hint={`${o.wins} won · ${o.losses} lost`} />
          <Stat label="Win rate" value={pct(o.win_rate)} />
          <Stat label="Profit factor" value={num(o.profit_factor)} valueClass={o.profit_factor !== null && o.profit_factor < 1 ? "text-loss" : undefined} />
          <Stat label="Expectancy" value={signed(o.expectancy, 4)} valueClass={pnlClass(o.expectancy)} hint="per trade" />
          <Stat label="Avg win" value={signed(o.avg_win, 4)} valueClass="text-profit" hint={o.avg_loss === null ? undefined : `avg loss ${signed(o.avg_loss, 4)}`} />
          <Stat label="Best trade" value={signed(o.best, 4)} valueClass="text-profit" hint={`worst ${signed(o.worst, 4)}`} />
          <Stat
            label="Max drawdown"
            value={start ? pct((100 * o.max_drawdown) / start, 2) : usd(o.max_drawdown, 4)}
            valueClass={o.max_drawdown ? "text-loss" : undefined}
            hint={o.avg_hold_hours === null ? undefined : `held ${duration(o.avg_hold_hours * 3600)} on avg`}
          />
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>
              <LineChart className="size-4 text-slate-400" /> Equity curve <span className="text-xs font-normal text-muted-foreground">after every closed trade</span>
            </CardTitle>
          </CardHeader>
          <CardContent>{stats.data?.curve.length ? <EquityCurve curve={stats.data.curve} start={start} opened={opened} /> : <Empty title="No closed structures yet" />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle><PieChart className="size-4 text-slate-400" /> Exit reasons</CardTitle>
          </CardHeader>
          <CardContent><ExitReasons rows={rows} /></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            <Trophy className="size-4 text-slate-400" /> Performance by strategy
            <span className="text-xs font-normal text-muted-foreground">best first · click a row to see its trades</span>
          </CardTitle>
        </CardHeader>
        {stats.data?.by_strategy.length ? (
          <StrategySection rows={stats.data.by_strategy} closed={rows} selected={strategy} onSelect={setStrategy} />
        ) : (
          <Empty title={`No closed ${broker} trades yet`} />
        )}
      </Card>

      {!!stats.data?.by_source.length && (
        <Card>
          <CardHeader>
            <CardTitle><Users className="size-4 text-slate-400" /> Auto vs manual</CardTitle>
          </CardHeader>
          <SummaryTable rows={stats.data.by_source} keyName="source" label="Source" />
        </Card>
      )}

      <div id="trades">
        <TradesTable rows={rows} loading={closed.isLoading} strategy={strategy} onStrategy={setStrategy} />
      </div>
    </>
  );
}
