"use client";

import { useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Compass,
  Layers,
  Loader2,
  Minus,
  RefreshCw,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { PriceChart, type Level } from "@/components/charts/price-chart";
import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { num, signed, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type {
  Bar,
  MarketAnalysisResponse,
  MarketOutlook,
  MarketPulse,
  MarketStructure,
  MarketVerdicts,
  SettingsResponse,
} from "@/lib/types";

export default function MarketDeskPage() {
  const { mutate } = useSWRConfig();
  const { data: settings } = useApi<SettingsResponse>("/settings");
  const underlyings = settings?.values.underlyings ?? ["BTC", "ETH"];
  const [underlying, setUnderlying] = useState("BTC");
  const [analysing, setAnalysing] = useState(false);

  const { data: analysis, error: analysisErr } = useApi<MarketAnalysisResponse>(
    `/desk/analysis?underlying=${underlying}`,
    { refreshInterval: 15_000 },
  );
  const { data: pulse } = useApi<MarketPulse>(`/desk/pulse?underlying=${underlying}`, {
    refreshInterval: 15_000,
  });
  const { data: outlook } = useApi<MarketOutlook>(`/desk/outlook?underlying=${underlying}`, {
    refreshInterval: 30_000,
  });
  const { data: structure } = useApi<MarketStructure>(`/desk/structure?underlying=${underlying}`, {
    refreshInterval: 30_000,
  });
  const { data: verdicts } = useApi<MarketVerdicts>(`/desk/verdicts?underlying=${underlying}&days=30`, {
    refreshInterval: 60_000,
  });
  const { data: chartData } = useApi<{ bars: Bar[] }>(
    `/desk/bars?underlying=${underlying}&interval=15m&days=5&limit=480`,
    { refreshInterval: 60_000 },
  );

  async function handleAnalyseNow() {
    setAnalysing(true);
    try {
      const res = await api<{ note: string }>("/desk/analysis", "POST");
      toast.success("Analysis updated", { description: res.note });
      mutate(`/desk/analysis?underlying=${underlying}`);
      mutate(`/desk/outlook?underlying=${underlying}`);
      mutate(`/desk/pulse?underlying=${underlying}`);
      mutate(`/desk/structure?underlying=${underlying}`);
      mutate(`/desk/verdicts?underlying=${underlying}&days=30`);
    } catch (err: unknown) {
      toast.error("Analysis failed", { description: err instanceof Error ? err.message : String(err) });
    } finally {
      setAnalysing(false);
    }
  }

  const chartLevels = useMemo<Level[]>(() => {
    const list: Level[] = [];
    if (!structure) return list;

    if (structure.profile?.poc) {
      list.push({ price: structure.profile.poc, label: "POC", color: "#3b82f6" });
    }
    if (structure.profile?.vah) {
      list.push({ price: structure.profile.vah, label: "VAH", color: "#64748b", dashed: true });
    }
    if (structure.profile?.val) {
      list.push({ price: structure.profile.val, label: "VAL", color: "#64748b", dashed: true });
    }

    structure.nearby?.above?.slice(0, 3).forEach((l) => {
      list.push({ price: l.price, label: l.label, color: "#ef4444", dashed: true });
    });
    structure.nearby?.below?.slice(0, 3).forEach((l) => {
      list.push({ price: l.price, label: l.label, color: "#10b981", dashed: true });
    });

    return list;
  }, [structure]);

  if (analysisErr) {
    return <LoadError error={analysisErr} />;
  }

  const stats = analysis?.stats;
  const currentPrice = analysis?.price ?? pulse?.price ?? 0;

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <PageHeader
        title="Market Desk"
        description="Multi-timeframe market regime, volume profile, liquidity zones, and algorithmic market verdicts."
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center rounded-lg border bg-muted/40 p-1">
              {underlyings.map((u) => (
                <button
                  key={u}
                  type="button"
                  onClick={() => setUnderlying(u)}
                  className={`rounded-md px-3 py-1 text-xs font-semibold transition-all ${
                    underlying === u
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {u}
                </button>
              ))}
            </div>

            <Button
              variant="outline"
              size="sm"
              onClick={handleAnalyseNow}
              disabled={analysing}
              className="gap-2 text-xs"
            >
              {analysing ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
              Analyse Now
            </Button>
          </div>
        }
      />

      {/* Live Market Stats Strip */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <div className="rounded-xl border bg-card p-3 shadow-sm">
          <div className="text-[11px] font-medium text-muted-foreground">{underlying} Mark Price</div>
          <div className="mt-1 font-mono text-lg font-bold">
            ${currentPrice ? usd(currentPrice, 2) : "—"}
          </div>
        </div>

        <div className="rounded-xl border bg-card p-3 shadow-sm">
          <div className="text-[11px] font-medium text-muted-foreground">24h Change</div>
          <div
            className={`mt-1 flex items-center gap-1 font-mono text-base font-semibold ${
              (stats?.change_24h_pct ?? 0) >= 0 ? "text-emerald-600" : "text-rose-600"
            }`}
          >
            {(stats?.change_24h_pct ?? 0) >= 0 ? (
              <ArrowUpRight className="size-4" />
            ) : (
              <ArrowDownRight className="size-4" />
            )}
            {signed(stats?.change_24h_pct, 2)}%
          </div>
        </div>

        <div className="rounded-xl border bg-card p-3 shadow-sm">
          <div className="text-[11px] font-medium text-muted-foreground">24h Range</div>
          <div className="mt-1 font-mono text-xs font-medium text-foreground">
            L: ${stats?.low_24h ? num(stats.low_24h, 0) : "—"}
            <span className="mx-1 text-muted-foreground">/</span>
            H: ${stats?.high_24h ? num(stats.high_24h, 0) : "—"}
          </div>
        </div>

        <div className="rounded-xl border bg-card p-3 shadow-sm">
          <div className="text-[11px] font-medium text-muted-foreground">Funding Rate</div>
          <div className="mt-1 font-mono text-base font-medium">
            {stats?.funding_rate_pct != null ? `${(stats.funding_rate_pct * 100).toFixed(4)}%` : "—"}
          </div>
        </div>

        <div className="rounded-xl border bg-card p-3 shadow-sm">
          <div className="text-[11px] font-medium text-muted-foreground">24h Turnover</div>
          <div className="mt-1 font-mono text-base font-medium">
            ${stats?.turnover_24h_usd ? num(stats.turnover_24h_usd / 1_000_000, 1) + "M" : "—"}
          </div>
        </div>

        <div className="rounded-xl border bg-card p-3 shadow-sm">
          <div className="text-[11px] font-medium text-muted-foreground">Open Interest</div>
          <div className="mt-1 font-mono text-base font-medium">
            ${stats?.oi_usd ? num(stats.oi_usd / 1_000_000, 1) + "M" : "—"}
          </div>
        </div>
      </div>

      {/* Market Pulse Banner */}
      {pulse && (
        <Card className="border-border/60 bg-gradient-to-r from-card via-card to-muted/20">
          <CardContent className="p-4 sm:p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="flex size-2 rounded-full bg-emerald-500 animate-pulse" />
                  <span className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">
                    Live Market Pulse
                  </span>
                  <Badge variant={pulse.loud ? "solid" : "default"} className="uppercase text-[10px]">
                    {pulse.state || "Active"}
                  </Badge>
                  {typeof pulse.way === "string" && pulse.way && (
                    <Badge variant={pulse.way === "up" ? "profit" : pulse.way === "down" ? "loss" : "outline"}>
                      {pulse.way.toUpperCase()}
                    </Badge>
                  )}
                  {Array.isArray(pulse.way) && pulse.way.length > 0 && pulse.way[0]?.edge !== null && pulse.way[0]?.edge !== undefined && (
                    <Badge variant={pulse.way[0].edge > 0 ? "profit" : pulse.way[0].edge < 0 ? "loss" : "outline"} className="text-[10px]">
                      {pulse.way[0].edge > 0 ? `+${(pulse.way[0].edge * 100).toFixed(1)}% edge` : `${(pulse.way[0].edge * 100).toFixed(1)}% edge`}
                    </Badge>
                  )}
                </div>
                <div className="text-base font-semibold text-foreground sm:text-lg">
                  {pulse.headline || "Analysing current market rhythm"}
                </div>
                {pulse.note && <p className="text-xs text-muted-foreground">{pulse.note}</p>}
              </div>

              {pulse.reasons && pulse.reasons.length > 0 && (
                <div className="flex flex-wrap gap-1.5 sm:max-w-md sm:justify-end">
                  {pulse.reasons.map((r, i) => (
                    <span
                      key={i}
                      className="rounded-md border bg-background/80 px-2 py-0.5 text-[11px] text-muted-foreground"
                    >
                      {r}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Chart Section */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <div className="space-y-0.5">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <BarChart3 className="size-4 text-primary" />
              {underlying} Perpetual Structure Chart (15m)
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              Candles with Point of Control (POC), Value Area (VAH/VAL), and confluence support/resistance levels.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="flex items-center gap-1 font-mono text-[11px] text-muted-foreground">
              <span className="size-2 rounded-full bg-blue-500" /> POC
            </span>
            <span className="flex items-center gap-1 font-mono text-[11px] text-muted-foreground">
              <span className="size-2 rounded-full bg-emerald-500" /> S
            </span>
            <span className="flex items-center gap-1 font-mono text-[11px] text-muted-foreground">
              <span className="size-2 rounded-full bg-rose-500" /> R
            </span>
          </div>
        </CardHeader>
        <CardContent className="pt-2">
          {chartData?.bars && chartData.bars.length > 0 ? (
            <PriceChart bars={chartData.bars} levels={chartLevels} height={320} />
          ) : (
            <div className="flex h-80 items-center justify-center">
              <Loading rows={4} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Main Analysis Tabs */}
      <Tabs defaultValue="outlook" className="space-y-4">
        <TabsList className="grid w-full grid-cols-4 sm:w-auto">
          <TabsTrigger value="outlook" className="gap-2 text-xs">
            <Compass className="size-3.5" /> Outlook
          </TabsTrigger>
          <TabsTrigger value="structure" className="gap-2 text-xs">
            <Layers className="size-3.5" /> Structure & Levels
          </TabsTrigger>
          <TabsTrigger value="verdicts" className="gap-2 text-xs">
            <Zap className="size-3.5" /> Verdicts ({verdicts?.verdicts?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="ai" className="gap-2 text-xs">
            <Sparkles className="size-3.5" /> Insights
          </TabsTrigger>
        </TabsList>

        {/* Outlook Tab */}
        <TabsContent value="outlook" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-3">
            {outlook?.summary?.map((s, idx) => {
              const isUp = s.direction > 0;
              const isDown = s.direction < 0;
              return (
                <Card key={idx} className="relative overflow-hidden">
                  <div
                    className={`absolute top-0 left-0 h-1 w-full ${
                      isUp ? "bg-emerald-500" : isDown ? "bg-rose-500" : "bg-slate-300"
                    }`}
                  />
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-muted-foreground uppercase">{s.label}</span>
                      <Badge variant={isUp ? "profit" : isDown ? "loss" : "default"}>
                        {isUp ? (
                          <span className="flex items-center gap-1">
                            <TrendingUp className="size-3" /> Bullish
                          </span>
                        ) : isDown ? (
                          <span className="flex items-center gap-1">
                            <TrendingDown className="size-3" /> Bearish
                          </span>
                        ) : (
                          <span className="flex items-center gap-1">
                            <Minus className="size-3" /> Neutral
                          </span>
                        )}
                      </Badge>
                    </div>
                    <CardTitle className="text-sm font-medium">{s.good_for}</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3 pt-0 text-xs">
                    <div className="flex justify-between border-b pb-1.5">
                      <span className="text-muted-foreground">Horizon</span>
                      <span className="font-medium">{s.horizon_label}</span>
                    </div>
                    <div className="flex justify-between border-b pb-1.5">
                      <span className="text-muted-foreground">Expected Move</span>
                      <span className="font-mono font-medium">
                        ±{s.move_pct}% (${num(s.move_usd, 0)})
                      </span>
                    </div>
                    <div className="flex justify-between border-b pb-1.5">
                      <span className="text-muted-foreground">Target Range</span>
                      <span className="font-mono font-medium">
                        ${num(s.band[0], 0)} – ${num(s.band[1], 0)}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Sample Size</span>
                      <span className="font-medium text-muted-foreground">{s.sample} bars</span>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {(!outlook?.summary || outlook.summary.length === 0) && (
            <Card>
              <CardContent className="py-8">
                <Empty title="No outlook summary available for this underlying." />
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Structure & Levels Tab */}
        <TabsContent value="structure" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            {/* Resistance Levels */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold text-rose-600 flex items-center gap-2">
                  <ArrowUpRight className="size-4" /> Levels & Resistances Above
                </CardTitle>
              </CardHeader>
              <CardContent>
                {structure?.nearby?.above && structure.nearby.above.length > 0 ? (
                  <div className="space-y-2">
                    {structure.nearby.above.map((lvl, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between rounded-lg border border-rose-100 bg-rose-50/30 px-3 py-2 text-xs"
                      >
                        <div>
                          <span className="font-mono font-bold text-rose-900">${num(lvl.price, 2)}</span>
                          <span className="ml-2 text-muted-foreground">{lvl.label}</span>
                        </div>
                        <Badge variant="outline" className="font-mono text-[10px]">
                          {lvl.confluence}x confluence
                        </Badge>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground py-4 text-center">No immediate overhead levels.</div>
                )}
              </CardContent>
            </Card>

            {/* Support Levels */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold text-emerald-600 flex items-center gap-2">
                  <ArrowDownRight className="size-4" /> Levels & Supports Below
                </CardTitle>
              </CardHeader>
              <CardContent>
                {structure?.nearby?.below && structure.nearby.below.length > 0 ? (
                  <div className="space-y-2">
                    {structure.nearby.below.map((lvl, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between rounded-lg border border-emerald-100 bg-emerald-50/30 px-3 py-2 text-xs"
                      >
                        <div>
                          <span className="font-mono font-bold text-emerald-900">${num(lvl.price, 2)}</span>
                          <span className="ml-2 text-muted-foreground">{lvl.label}</span>
                        </div>
                        <Badge variant="outline" className="font-mono text-[10px]">
                          {lvl.confluence}x confluence
                        </Badge>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground py-4 text-center">No immediate support levels.</div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Volume Profile & ATR details */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Card className="p-3">
              <div className="text-[11px] font-medium text-muted-foreground">Volume Profile POC</div>
              <div className="mt-1 font-mono text-base font-semibold text-blue-600">
                ${structure?.profile?.poc ? num(structure.profile.poc, 2) : "—"}
              </div>
            </Card>
            <Card className="p-3">
              <div className="text-[11px] font-medium text-muted-foreground">Value Area High (VAH)</div>
              <div className="mt-1 font-mono text-base font-medium">
                ${structure?.profile?.vah ? num(structure.profile.vah, 2) : "—"}
              </div>
            </Card>
            <Card className="p-3">
              <div className="text-[11px] font-medium text-muted-foreground">Value Area Low (VAL)</div>
              <div className="mt-1 font-mono text-base font-medium">
                ${structure?.profile?.val ? num(structure.profile.val, 2) : "—"}
              </div>
            </Card>
            <Card className="p-3">
              <div className="text-[11px] font-medium text-muted-foreground">Current ATR (14)</div>
              <div className="mt-1 font-mono text-base font-medium">
                ${structure?.atr ? num(structure.atr, 2) : "—"}
              </div>
            </Card>
          </div>
        </TabsContent>

        {/* Verdicts Tab */}
        <TabsContent value="verdicts" className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {verdicts?.verdicts?.map((v, i) => {
              const isBull = v.stance.includes("bull") || v.stance === "up";
              const isBear = v.stance.includes("bear") || v.stance === "down";
              return (
                <Card key={i} className="flex flex-col justify-between">
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-[11px] text-muted-foreground uppercase">{v.measure}</span>
                      <Badge variant={isBull ? "profit" : isBear ? "loss" : "default"} className="text-[10px]">
                        {String(v.stance || "").replace("_", " ").toUpperCase()}
                      </Badge>
                    </div>
                    <CardTitle className="text-sm font-semibold">{v.label}</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3 pt-0 text-xs">
                    <p className="text-muted-foreground leading-relaxed">{v.detail}</p>
                    <div className="flex items-center justify-between border-t pt-2 text-[11px]">
                      <span className="text-muted-foreground">Confidence</span>
                      <span className="font-mono font-semibold">{(v.confidence * 100).toFixed(0)}%</span>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {(!verdicts?.verdicts || verdicts.verdicts.length === 0) && (
            <Card>
              <CardContent className="py-8">
                <Empty title="No verdicts recorded yet. Run 'Analyse Now' to trigger a scan." />
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* AI & Rule Insights Tab */}
        <TabsContent value="ai" className="space-y-4">
          {analysis?.analysis?.insights && analysis.analysis.insights.length > 0 ? (
            <div className="space-y-3">
              {analysis.analysis.insights.map((ins, i) => (
                <Card key={i}>
                  <CardContent className="flex items-start justify-between gap-4 p-4">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold">{ins.label}</span>
                        <Badge variant={ins.stance === "bullish" ? "profit" : ins.stance === "bearish" ? "loss" : "default"}>
                          {ins.stance}
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">{ins.detail}</p>
                    </div>
                    <Badge variant="outline" className="text-[10px]">
                      {ins.importance}
                    </Badge>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <Card>
              <CardContent className="py-8">
                <Empty title="No active market insights. Click 'Analyse Now' above to generate fresh context." />
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
