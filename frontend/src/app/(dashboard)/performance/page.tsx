"use client";

// The home page, one place for results: a day at the top (today, or the day opened from the calendar
// or a chart: ?date=YYYY-MM-DD), then every closed trade cut any way, then the trades themselves.
// Any trade, here or anywhere, opens the same detail.

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { AnalysisSection } from "@/components/analysis/section";
import { dayKey } from "@/components/performance/derive";
import { DaySection } from "@/components/performance/today-section";
import { TradesTable } from "@/components/performance/trades-table";
import { PositionDetail } from "@/components/positions/position-detail";
import { Confirm } from "@/components/shared/confirm";
import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { pct, signedPct, titleCase, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Accounts, Broker, SettingsResponse, Stats, StrategiesResponse, Structure } from "@/lib/types";

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function Performance() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [broker, setBroker] = useState<Broker>("paper");
  const [strategy, setStrategy] = useState("");
  const [detail, setDetail] = useState<string | null>(null);
  const accounts = useApi<Accounts>("/portfolio/accounts", { refreshInterval: 15_000 });
  const settings = useApi<SettingsResponse>("/settings");
  const catalog = useApi<StrategiesResponse>("/strategies");
  const stats = useApi<Stats>(`/portfolio/stats?broker=${broker}`, { refreshInterval: 15_000 });
  const closed = useApi<Structure[]>(`/trading/structures?status=closed&broker=${broker}&limit=1000`, { refreshInterval: 15_000 });

  const tz = String(settings.data?.values.day_timezone ?? "Asia/Kolkata");
  const today = dayKey(tz);
  const asked = params.get("date") ?? "";
  const date = DATE.test(asked) && asked <= today ? asked : today;
  const openDay = (day: string) => {
    router.replace(day === today ? pathname : `${pathname}?date=${day}`, { scroll: false });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const raw = accounts.data?.accounts[broker];
  const account = raw && !("error" in raw) ? raw : undefined;
  const start = broker === "paper" && account && typeof account.extra.starting_balance === "number" ? account.extra.starting_balance : 0;
  const o = stats.data?.overall;
  const rows = useMemo(() => closed.data ?? [], [closed.data]);
  const labels = useMemo(() => {
    const out: Record<string, string> = Object.fromEntries((catalog.data?.strategies ?? []).map((s) => [s.name, s.label]));
    for (const s of rows) out[s.strategy] ??= s.strategy === "custom" || s.strategy === "manual" ? `Hand-built (${s.strategy})` : titleCase(s.strategy);
    return out;
  }, [catalog.data, rows]);

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
      <PageHeader
        title="Performance"
        description={
          o ? (
            <>
              {start ? <>Realised balance <b className="num text-foreground">{usd(start + o.pnl)}</b> ({signedPct((100 * o.pnl) / start)} on {usd(start, 0)}) · </> : null}
              {o.trades} closed trades · max drawdown <b className="num text-foreground">{start ? pct((100 * o.max_drawdown) / start, 1) : usd(o.max_drawdown)}</b>
            </>
          ) : "A day at a time, then every closed trade after every fee."
        }
        actions={
          <Tabs value={broker} onValueChange={(v) => { setBroker(v as Broker); setStrategy(""); }}>
            <TabsList>
              <TabsTrigger value="paper">Paper</TabsTrigger>
              <TabsTrigger value="delta">Delta</TabsTrigger>
            </TabsList>
          </Tabs>
        }
      />

      <LoadError error={accounts.error ?? (raw && "error" in raw ? raw.error : undefined)} />
      {!accounts.data ? (
        <Loading rows={3} />
      ) : !raw ? (
        <Empty title={`${broker === "paper" ? "Paper" : "Delta"} is not active`}>
          Switch it on in <Link href="/settings" className="text-primary hover:underline">Settings</Link>.
        </Empty>
      ) : (
        <>
          <DaySection broker={broker} date={date} today={today} tz={tz} accounts={accounts.data} onDay={openDay} />
          <LoadError error={closed.error ?? stats.error} />
          {closed.data && <AnalysisSection trades={rows} tz={tz} labels={labels} date={date} today={today} onDay={openDay} onOpen={(s) => setDetail(s.id)} />}
          <div id="trades">
            <TradesTable rows={rows} loading={closed.isLoading} strategy={strategy} onStrategy={setStrategy} onOpen={(s) => setDetail(s.id)} />
          </div>
        </>
      )}

      {broker === "paper" && account && (
        <div className="flex justify-end">
          <Confirm
            title="Reset the paper account?"
            description="The whole paper trade history is deleted and the balance goes back to the starting balance from Settings. Refused while a paper position is open."
            confirmLabel="Reset"
            destructive
            onConfirm={reset}
            trigger={(openDialog) => (
              <Button variant="ghost" size="sm" onClick={openDialog} className="text-muted-foreground">
                <RotateCcw /> Reset paper account
              </Button>
            )}
          />
        </div>
      )}

      <PositionDetail id={detail} onClose={() => setDetail(null)} />
    </>
  );
}

export default function PerformancePage() {
  // The selected day lives in the address (?date=), which a static page reads only in the browser.
  return (
    <Suspense fallback={<Loading rows={4} />}>
      <Performance />
    </Suspense>
  );
}
