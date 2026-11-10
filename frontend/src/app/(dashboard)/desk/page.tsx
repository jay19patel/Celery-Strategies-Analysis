"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { CandlestickChart, Grid3x3, Loader2, Ticket } from "lucide-react";

import { PriceChart, type Level } from "@/components/charts/price-chart";
import { AiBriefCard } from "@/components/desk/ai-brief-card";
import { ChainTable, type PickedLeg } from "@/components/desk/chain-table";
import { ExpiryPicker, MarketStats } from "@/components/desk/market-bar";
import { OptionIdeasCard } from "@/components/desk/option-ideas";
import { OrderPanel } from "@/components/desk/order-panel";
import { PreviewPanel } from "@/components/desk/preview-panel";
import { RegimeCard } from "@/components/desk/regime-card";
import { StrategyCatalog } from "@/components/desk/strategy-catalog";
import { StrategyPicker, type TicketParams } from "@/components/desk/strategy-picker";
import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { useApi } from "@/lib/live";
import type { Accounts, Bar, Overview, Preview, SettingsResponse, Side, StrategiesResponse } from "@/lib/types";

export default function DeskPage() {
  const { data: settings } = useApi<SettingsResponse>("/settings");
  const underlyings = settings?.values.underlyings ?? ["BTC"];
  const [chosenUnderlying, setUnderlying] = useState<string | null>(null);
  // The one chosen while it is still enabled, else the first enabled one: never a disabled market.
  const underlying = chosenUnderlying && underlyings.includes(chosenUnderlying) ? chosenUnderlying : underlyings[0];
  const [chosenExpiry, setExpiry] = useState<number | null>(null);
  // A ticket starts from each strategy's configured params; `overrides` holds only what was typed here.
  const [params, setParams] = useState<TicketParams>({ strategy: "", qty: 1, overrides: {}, legs: [] });

  const { data: overview, error: overviewError } = useApi<Overview>(`/market/overview?underlying=${underlying}`, { refreshInterval: 15_000 });
  const { data: catalog } = useApi<StrategiesResponse>("/strategies");
  const strategies = catalog?.strategies.filter((s) => s.family === "options");
  const strategy = params.strategy || strategies?.[0]?.name || "custom";
  const { data: accounts } = useApi<Accounts>("/portfolio/accounts");
  const { data: bars } = useApi<{ bars: Bar[] }>(`/market/bars?underlying=${underlying}&limit=384`, { refreshInterval: 60_000 });


  // The chosen expiry while it is still listed; once it settles (or before one is chosen), the default.
  const expiry = overview ? (overview.expiries.some((e) => e.expiry === chosenExpiry) ? chosenExpiry : overview.default_expiry) : null;

  const body = useMemo(() => {
    if (!expiry) return null;
    if (strategy === "custom") {
      if (!params.legs.length) return null;
      return { underlying, strategy: "custom", legs: params.legs, qty: params.qty };
    }
    return { underlying, strategy, expiry, qty: params.qty, params: params.overrides };
  }, [underlying, expiry, strategy, params.legs, params.qty, params.overrides]);

  const preview = useSWR<Preview>(body ? ["/strategies/preview", JSON.stringify(body)] : null, ([path, b]: [string, string]) => api<Preview>(path, "POST", JSON.parse(b)), {
    refreshInterval: 15_000,
    keepPreviousData: true,
    shouldRetryOnError: false,
  });

  const picked = useMemo(() => {
    const legs = strategy === "custom" ? params.legs : (preview.data?.plan.legs ?? []);
    return new Map<string, Side>(legs.map((l) => [l.symbol, l.side]));
  }, [strategy, params.legs, preview.data]);

  const pick = (symbol: string, side: Side) => {
    setParams((p) => {
      const base: PickedLeg[] = strategy === "custom" ? p.legs : (preview.data?.plan.legs.map((l) => ({ symbol: l.symbol, side: l.side, ratio: l.ratio })) ?? []);
      const existing = base.find((l) => l.symbol === symbol);
      const legs = existing
        ? existing.side === side
          ? base.filter((l) => l.symbol !== symbol)
          : base.map((l) => (l.symbol === symbol ? { ...l, side } : l))
        : [...base, { symbol, side, ratio: 1 }].slice(0, 6);
      return { ...p, strategy: "custom", legs };
    });
  };

  const levels: Level[] = useMemo(() => {
    const out: Level[] = [];
    const p = preview.data;
    if (!p) return out;
    for (const l of p.plan.legs) out.push({ price: l.strike, label: `${l.side === "sell" ? "S" : "B"} ${l.kind[0].toUpperCase()}`, color: l.side === "sell" ? "#cf1f2e" : "#0e8a3e", dashed: true });
    for (const b of p.analysis.breakevens) out.push({ price: b, label: "BE", color: "#64748b" });
    return out;
  }, [preview.data]);

  return (
    <>
      <PageHeader title="Options Desk" description="Build a structure from the live chain, check it, and place it on paper or Delta." />
      <div className="flex flex-wrap items-center gap-3">
        {underlyings.length > 1 && (
          <Tabs value={underlying} onValueChange={(v) => { setUnderlying(v); setExpiry(null); setParams((p) => ({ ...p, legs: [] })); }}>
            <TabsList>
              {underlyings.map((u) => (
                <TabsTrigger key={u} value={u}>{u}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        )}
        {overview && expiry && <ExpiryPicker overview={overview} expiry={expiry} onChange={(e) => { setExpiry(e); setParams((p) => ({ ...p, legs: [] })); }} />}
      </div>

      <LoadError error={overviewError} />
      {!overview && !overviewError && <Loading rows={2} />}
      {overview && expiry && <MarketStats overview={overview} expiry={expiry} />}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <Card>
            <CardHeader>
              <CardTitle>
                <Ticket className="size-4 text-primary" /> Strategy builder
              </CardTitle>
              {preview.isValidating && <Loader2 className="size-4 animate-spin text-slate-400" />}
            </CardHeader>
            <CardContent className="space-y-5">
              {strategies && <StrategyPicker strategies={strategies} params={{ ...params, strategy }} onChange={(c) => setParams((p) => ({ ...p, ...c }))} />}
              <LoadError error={preview.error} />
              {!body && strategy === "custom" && <Empty title="No legs yet">Pick contracts from the option chain below.</Empty>}
              {preview.data && body && (
                <>
                  <PreviewPanel preview={preview.data} />
                  <div className="border-t pt-4">
                    <OrderPanel preview={preview.data} brokers={accounts?.brokers ?? []} onQty={(qty) => setParams((p) => ({ ...p, qty }))} />
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-5">
          {expiry && strategies && (
            <RegimeCard
              underlying={underlying}
              expiry={expiry}
              strategies={strategies}
              minScore={catalog?.min_score ?? 60}
              selected={strategy}
              onPick={(strategy) => setParams((p) => ({ ...p, strategy }))}
            />
          )}
          <AiBriefCard underlying={underlying} />
        </div>
      </div>

      {/* The wide reads: each wants the whole page, not a column beside the rail. */}
      <Card>
        <CardHeader>
          <CardTitle>
            <CandlestickChart className="size-4 text-primary" /> {overview?.perpetual ?? underlying} · 15m
          </CardTitle>
          <span className="text-[11.5px] text-muted-foreground">strikes and breakevens of the structure above</span>
        </CardHeader>
        <CardContent className="p-2">
          {bars?.bars.length ? <PriceChart bars={bars.bars} levels={levels} height={460} /> : <Loading rows={4} className="p-2" />}
        </CardContent>
      </Card>

      {expiry && <OptionIdeasCard underlying={underlying} expiry={expiry} />}

      <Card>
        <CardHeader>
          <CardTitle>
            <Grid3x3 className="size-4 text-primary" /> Option chain
          </CardTitle>
          <span className="text-[11.5px] text-muted-foreground">click a bid to sell, an ask to buy</span>
        </CardHeader>
        {expiry && <ChainTable underlying={underlying} expiry={expiry} picked={picked} onPick={pick} />}
      </Card>

      {strategies && <StrategyCatalog strategies={strategies} selected={strategy} onPick={(name) => setParams((p) => ({ ...p, strategy: name, overrides: {}, legs: [] }))} />}
    </>
  );
}
