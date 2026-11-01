"use client";

import { useMemo, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Calendar,
  Clock,
  Compass,
  Globe,
  Layers,
  Loader2,
  Minus,
  RefreshCw,
  Scale,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { PcrHistoryChart } from "@/components/charts/pcr-history-chart";
import { PriceChart, type ChartMarker, type Level } from "@/components/charts/price-chart";
import { TrendlineStructureChart } from "@/components/charts/trendline-structure-chart";
import { AiBriefCard } from "@/components/desk/ai-brief-card";
import { DirectPerpPanel } from "@/components/desk/direct-perp-panel";
import { FibonacciCard } from "@/components/desk/fibonacci-card";
import { TrendlinesTable } from "@/components/desk/trendlines-table";
import { PageHeader } from "@/components/shared/page";
import { Rolling } from "@/components/shared/rolling";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { ago, num, signed, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type {
  Bar,
  MarketAnalysisResponse,
  MarketOutlook,
  MarketPulse,
  MarketStructure,
  MarketVerdicts,
  Overview,
  SettingsResponse,
  StructureZone,
  Fib,
  TrendlinesResponse,
} from "@/lib/types";

interface SwingEvent {
  kind: "choch" | "bos";
  direction: "up" | "down";
  bar_time: number;
  level: number;
  close: number;
  displacement: number;
  volume_ratio: number;
}

interface GapEvent {
  direction: "up" | "down";
  low: number;
  high: number;
  bar_time: number;
  filled: boolean;
}

function SymbolPriceCard({
  sym,
  active,
  data,
  onSelect,
}: {
  sym: "BTC" | "ETH";
  active: boolean;
  data: Overview | undefined;
  onSelect: () => void;
}) {
  const s = data?.stats;
  const last = data?.mark ?? (data?.spot ?? 0);
  const chg = s?.change_24h_pct ?? 0;
  const isUp = chg >= 0;
  const low = s?.low_24h ?? 0;
  const high = s?.high_24h ?? 0;
  const inRange = high > low ? Math.max(0, Math.min(100, ((last - low) / (high - low)) * 100)) : 50;
  const spread = s?.ask && s?.bid ? s.ask - s.bid : null;
  const spreadPct = spread && last ? (spread / last) * 100 : null;
  const basis = data?.mark && data?.spot ? ((data.mark - data.spot) / data.spot) * 100 : null;

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      className={`relative w-full rounded-2xl p-4 text-left outline-none transition-[border-color,background-color,box-shadow] duration-150 focus-visible:ring-[3px] focus-visible:ring-ring/50 ${
        active ? "bg-card ring-2 ring-primary" : "bg-card hover:ring-1 hover:ring-separator"
      }`}
    >
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-bold text-sm tracking-wide text-foreground">{sym}USD</span>
            <span className="text-[11px] text-muted-foreground">perpetual</span>
          </div>
          <div className="num mt-1.5 text-2xl font-semibold tracking-tight text-foreground">
            {last ? <Rolling text={`$${usd(last, sym === "BTC" ? 1 : 2)}`} /> : "—"}
          </div>
        </div>

        <span
          className={`num flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
            isUp
              ? "bg-profit-soft text-profit"
              : "bg-loss-soft text-loss"
          }`}
        >
          {isUp ? <ArrowUpRight className="size-3.5" /> : <ArrowDownRight className="size-3.5" />}
          {signed(chg, 2)}% <span className="font-normal opacity-70">24h</span>
        </span>
      </div>

      {/* 24h Range Bar Slider */}
      <div className="mt-3 space-y-1">
        <div className="num flex justify-between text-[11px] text-muted-foreground">
          <span>
            24h Low <b className="text-foreground">${low ? num(low, 0) : "—"}</b>
          </span>
          <span>
            High <b className="text-foreground">${high ? num(high, 0) : "—"}</b>
          </span>
        </div>
        <div className="relative h-1.5 w-full rounded-full bg-muted">
          <span
            className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-slate-900 shadow-sm"
            style={{ left: `${inRange}%` }}
          />
        </div>
      </div>

      {/* 3x3 Stats Grid from v3 */}
      <div className="mt-3.5 grid grid-cols-3 gap-x-2 gap-y-2.5 border-t pt-3 text-[11px]">
        <div>
          <div className="text-[11px] text-muted-foreground">Mark</div>
          <div className="num font-semibold text-foreground">${data?.mark ? usd(data.mark, 1) : "—"}</div>
        </div>
        <div>
          <div className="text-[11px] text-muted-foreground">Index / Spot</div>
          <div className="num font-semibold text-foreground">${data?.spot ? usd(data.spot, 1) : "—"}</div>
        </div>
        <div>
          <div className="text-[11px] text-muted-foreground" title="Mark against index">
            Basis
          </div>
          <div
            className={`num font-semibold ${
              basis && basis > 0 ? "text-profit" : basis && basis < 0 ? "text-loss" : ""
            }`}
          >
            {basis != null ? `${signed(basis, 3)}%` : "—"}
          </div>
        </div>
        <div>
          <div className="text-[11px] text-muted-foreground">24h Open</div>
          <div className="num font-semibold text-foreground">${s?.open_24h ? num(s.open_24h, 0) : "—"}</div>
        </div>
        <div>
          <div className="text-[11px] text-muted-foreground">24h Turnover</div>
          <div className="num font-semibold text-foreground">
            ${s?.turnover_24h_usd ? (s.turnover_24h_usd / 1_000_000).toFixed(1) + "M" : "—"}
          </div>
        </div>
        <div>
          <div className="text-[11px] text-muted-foreground">Open Interest</div>
          <div className="num font-semibold text-foreground">
            ${s?.oi_usd ? (s.oi_usd / 1_000_000).toFixed(1) + "M" : "—"}
          </div>
        </div>
        <div>
          <div className="text-[11px] text-muted-foreground">Bid</div>
          <div className="num font-semibold text-profit">${s?.bid ? usd(s.bid, 1) : "—"}</div>
        </div>
        <div>
          <div className="text-[11px] text-muted-foreground">Ask</div>
          <div className="num font-semibold text-loss">${s?.ask ? usd(s.ask, 1) : "—"}</div>
        </div>
        <div>
          <div className="text-[11px] text-muted-foreground">Spread</div>
          <div className="num font-semibold text-foreground">
            {spread != null ? `$${num(spread, 1)}` : "—"}
            {spreadPct != null && (
              <span className="text-[9.5px] text-muted-foreground ml-1">({spreadPct.toFixed(3)}%)</span>
            )}
          </div>
        </div>
      </div>

      {/* Funding rate strip */}
      <div className="mt-2.5 flex items-center justify-between border-t pt-2 text-[11px]">
        <span className="text-muted-foreground">8h funding</span>
        <span
          className={`num font-semibold ${(s?.funding_rate_pct ?? 0) >= 0 ? "text-loss" : "text-profit"}`}
        >
          {s?.funding_rate_pct != null ? `${(s.funding_rate_pct * 100).toFixed(4)}%` : "—"}
          <span className="ml-1 text-[10px] text-muted-foreground font-normal">
            {(s?.funding_rate_pct ?? 0) > 0 ? "(longs pay)" : (s?.funding_rate_pct ?? 0) < 0 ? "(shorts pay)" : ""}
          </span>
        </span>
      </div>
    </button>
  );
}

export default function MarketDeskPage() {
  const { mutate } = useSWRConfig();
  const { data: settings } = useApi<SettingsResponse>("/settings");
  const underlyings = settings?.values.underlyings ?? ["BTC", "ETH"];
  const [underlying, setUnderlying] = useState("BTC");
  const [chartInterval, setChartInterval] = useState<"15m" | "1h" | "1d">("15m");
  const [timezone, setTimezone] = useState<"Local" | "UTC" | "IST">("IST");
  const [analysing, setAnalysing] = useState(false);

  // Chart layer visibility state (Default all ON as requested, toggleable anytime)
  const [layers, setLayers] = useState<{
    fvg: boolean;
    ob: boolean;
    smc: boolean;
    levels: boolean;
    volume: boolean;
    trendlines: boolean;
    clusters: boolean;
    ema: boolean;
  }>(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = localStorage.getItem("tradebuddy_chart_layers");
        if (saved) {
          const parsed = JSON.parse(saved);
          return {
            fvg: parsed.fvg ?? true,
            ob: parsed.ob ?? true,
            smc: parsed.smc ?? true,
            levels: parsed.levels ?? true,
            volume: parsed.volume ?? true,
            trendlines: parsed.trendlines ?? true,
            clusters: parsed.clusters ?? true,
            ema: parsed.ema ?? true,
          };
        }
      } catch {
        /* ignore private mode */
      }
    }
    return { fvg: true, ob: true, smc: true, levels: true, volume: true, trendlines: true, clusters: true, ema: true };
  });

  const toggleLayer = (layerKey: "fvg" | "ob" | "smc" | "levels" | "volume" | "trendlines" | "clusters" | "ema") => {
    setLayers((prev) => {
      const next = { ...prev, [layerKey]: !prev[layerKey] };
      try {
        localStorage.setItem("tradebuddy_chart_layers", JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  // Set of individual trendline or cluster IDs hidden from the chart
  const [hiddenItemIds, setHiddenItemIds] = useState<Set<string>>(new Set());
  const toggleItemVisibility = (id: string) => {
    setHiddenItemIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

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

  const barParams = useMemo(() => {
    if (chartInterval === "1h") return { days: 30, limit: 720 };
    if (chartInterval === "1d") return { days: 180, limit: 365 };
    return { days: 5, limit: 480 };
  }, [chartInterval]);

  const { data: chartData } = useApi<{ bars: Bar[] }>(
    `/desk/bars?underlying=${underlying}&interval=${chartInterval}&days=${barParams.days}&limit=${barParams.limit}`,
    { refreshInterval: 60_000 },
  );

  const { data: fibData } = useApi<Fib>(`/desk/fibonacci?underlying=${underlying}&interval=${chartInterval}&limit=500`, {
    refreshInterval: 60_000,
  });
  const { data: trendlinesData } = useApi<TrendlinesResponse>(
    `/desk/trendlines?underlying=${underlying}&interval=${chartInterval}&limit=500`,
    { refreshInterval: 30_000 },
  );

  const { data: btcOverview } = useApi<Overview>("/market/overview?underlying=BTC", {
    refreshInterval: 10_000,
  });
  const { data: ethOverview } = useApi<Overview>("/market/overview?underlying=ETH", {
    refreshInterval: 10_000,
  });

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
      mutate(`/desk/options-history?underlying=${underlying}&hours=24`);
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

  // Extract all institutional zones (FVGs & OBs) from engine structure
  const structureZones = useMemo<StructureZone[]>(() => {
    return structure?.zones?.zones || [];
  }, [structure]);

  // Active Fair Value Gaps (15m imbalances)
  const fvgZones = useMemo<StructureZone[]>(() => {
    return structureZones.filter((z) => z.kind === "fvg" && z.state !== "invalidated");
  }, [structureZones]);

  // Active Order Blocks
  const obZones = useMemo<StructureZone[]>(() => {
    return structureZones.filter((z) => z.kind === "ob" && z.state !== "invalidated");
  }, [structureZones]);

  // Nearest Overhead Resistance FVG
  const nearestFvgAbove = useMemo<StructureZone | null>(() => {
    const ab = fvgZones.filter((z) => z.distance_pct != null && z.distance_pct > 0);
    return ab.length ? ab.reduce((prev, curr) => ((curr.distance_pct ?? 999) < (prev.distance_pct ?? 999) ? curr : prev)) : null;
  }, [fvgZones]);

  // Nearest Below Support FVG
  const nearestFvgBelow = useMemo<StructureZone | null>(() => {
    const bel = fvgZones.filter((z) => z.distance_pct != null && z.distance_pct < 0);
    return bel.length ? bel.reduce((prev, curr) => ((curr.distance_pct ?? -999) > (prev.distance_pct ?? -999) ? curr : prev)) : null;
  }, [fvgZones]);

  // Extract recent SMC swings (CHoCH / BOS) and Gaps from 15m timeframe
  const swings = useMemo<SwingEvent[]>(() => {
    const tf = outlook?.timeframes?.[0] as { swings?: SwingEvent[] } | undefined;
    return (tf?.swings ?? []).slice(-8).reverse();
  }, [outlook]);

  const gaps = useMemo<GapEvent[]>(() => {
    const tf = outlook?.timeframes?.[0] as { gaps?: GapEvent[] } | undefined;
    return (tf?.gaps ?? []).slice(-6).reverse();
  }, [outlook]);

  // Combine Smart Money Concepts breaks for Candlestick chart markers
  const chartMarkers = useMemo<ChartMarker[]>(() => {
    const list: ChartMarker[] = [];
    const smcBreaks = (structure?.smc?.breaks || []) as Array<{
      kind: "choch" | "bos";
      direction: "up" | "down";
      bar_time: number;
      level: number;
    }>;
    if (smcBreaks.length > 0) {
      smcBreaks.slice(-40).forEach((b) => {
        list.push({
          time: b.bar_time,
          kind: b.kind,
          direction: b.direction,
          level: b.level,
        });
      });
    } else if (swings.length > 0) {
      swings.forEach((s) => {
        list.push({
          time: s.bar_time,
          kind: s.kind,
          direction: s.direction,
          level: s.level,
        });
      });
    }
    return list;
  }, [structure, swings]);

  // Filtered trendlines and clusters for the chart based on master toggles and per-item toggles
  const activeTrendlines = useMemo(() => {
    if (!layers.trendlines) return [];
    return (trendlinesData?.trendlines || []).filter((t) => !hiddenItemIds.has(t.id));
  }, [layers.trendlines, trendlinesData?.trendlines, hiddenItemIds]);

  const activeClusters = useMemo(() => {
    if (!layers.clusters) return [];
    return (trendlinesData?.horizontal_clusters || []).filter((c) => !hiddenItemIds.has(c.id));
  }, [layers.clusters, trendlinesData?.horizontal_clusters, hiddenItemIds]);

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
          <div className="flex flex-wrap items-center gap-2.5">
            {/* Timezone Selector */}
            <div className="flex items-center rounded-lg bg-secondary/60 p-1 text-xs">
              <span className="flex items-center gap-1 px-1.5 text-muted-foreground">
                <Globe className="size-3" />
              </span>
              {(["Local", "UTC", "IST"] as const).map((tz) => (
                <button
                  key={tz}
                  type="button"
                  onClick={() => setTimezone(tz)}
                  className={`rounded-md px-2 py-0.5 font-mono text-[11px] font-semibold transition-colors ${
                    timezone === tz
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {tz}
                </button>
              ))}
            </div>

            {/* Underlying Selector */}
            <div className="flex items-center rounded-lg bg-secondary/60 p-1">
              {underlyings.map((u) => (
                <button
                  key={u}
                  type="button"
                  onClick={() => setUnderlying(u)}
                  className={`rounded-md px-3 py-1 text-xs font-semibold transition-colors ${
                    underlying === u
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {u}
                </button>
              ))}
            </div>

            {/* Manual Run Scan */}
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

      {/* 24h Dual Rolling Price Cards (from v3) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <SymbolPriceCard
          sym="BTC"
          active={underlying === "BTC"}
          data={btcOverview}
          onSelect={() => setUnderlying("BTC")}
        />
        <SymbolPriceCard
          sym="ETH"
          active={underlying === "ETH"}
          data={ethOverview}
          onSelect={() => setUnderlying("ETH")}
        />
      </div>

      {/* Market Pulse Banner */}
      {pulse && (
        <Card>
          <CardContent className="p-4 sm:p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-muted-foreground">Market pulse</span>
                  <Badge variant={pulse.loud ? "warn" : "default"}>{pulse.state || "Active"}</Badge>
                  {typeof pulse.way === "string" && pulse.way && (
                    <Badge variant={pulse.way === "up" ? "profit" : pulse.way === "down" ? "loss" : "outline"}>
                      {pulse.way}
                    </Badge>
                  )}
                  {Array.isArray(pulse.way) && pulse.way.length > 0 && pulse.way[0]?.edge !== null && pulse.way[0]?.edge !== undefined && (
                    <Badge variant={pulse.way[0].edge > 0 ? "profit" : pulse.way[0].edge < 0 ? "loss" : "outline"} className="num">
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
                      className="rounded-md border bg-muted/50 px-2 py-0.5 text-[11px] text-muted-foreground"
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

      {/* The tape, full width — the page's primary read, not a column beside the rail */}
      <Card>
        <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between pb-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="space-y-0.5">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <BarChart3 className="size-4 text-primary" />
                {underlying} Perpetual Structure Chart ({chartInterval})
              </CardTitle>
              <p className="text-xs text-muted-foreground">
                Candlestick tape with dynamic Trendlines, Fair Value Gaps (FVG), Order Blocks, and Volume Profile.
              </p>
            </div>

            {/* Multi-Timeframe Switcher (15m, 1h, 1d) */}
            <div className="flex items-center rounded-lg bg-secondary/60 p-1">
              {(["15m", "1h", "1d"] as const).map((tf) => (
                <button
                  key={tf}
                  type="button"
                  onClick={() => setChartInterval(tf)}
                  className={`rounded-md px-2.5 py-0.5 font-mono text-xs font-semibold transition-colors ${
                    chartInterval === tf
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>

          {/* Layer Controls Bar (Default ALL ON, click to toggle) */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-0.5 text-[11px] font-medium text-muted-foreground">Layers</span>

            <Button
              variant="outline"
              size="sm"
              aria-pressed={layers.fvg}
              onClick={() => toggleLayer("fvg")}
              className={`h-7 px-2.5 text-xs ${
                layers.fvg ? "border-slate-300 bg-muted text-foreground" : "text-muted-foreground"
              }`}
              title="Toggle Fair Value Gaps (shaded imbalance zones)"
            >
              <span className="size-2 rounded-full bg-purple-500" />
              FVG ({fvgZones.length})
            </Button>

            <Button
              variant="outline"
              size="sm"
              aria-pressed={layers.ob}
              onClick={() => toggleLayer("ob")}
              className={`h-7 px-2.5 text-xs ${
                layers.ob ? "border-slate-300 bg-muted text-foreground" : "text-muted-foreground"
              }`}
              title="Toggle Order Blocks"
            >
              <span className="size-2 rounded-full bg-orange-500" />
              OB ({obZones.length})
            </Button>

            <Button
              variant="outline"
              size="sm"
              aria-pressed={layers.ema}
              onClick={() => toggleLayer("ema")}
              className={`h-7 px-2.5 text-xs ${
                layers.ema ? "border-slate-300 bg-muted text-foreground" : "text-muted-foreground"
              }`}
              title="Toggle EMA 20 (yellow) & EMA 50 (red)"
            >
              <span className="size-2 rounded-full bg-amber-500" />
              EMA (20/50)
            </Button>

            <Button
              variant="outline"
              size="sm"
              aria-pressed={layers.smc}
              onClick={() => toggleLayer("smc")}
              className={`h-7 px-2.5 text-xs ${
                layers.smc ? "border-slate-300 bg-muted text-foreground" : "text-muted-foreground"
              }`}
              title="Toggle Smart Money Concepts (BOS / CHoCH break markers)"
            >
              <span className="size-2 rounded-full bg-emerald-500" />
              SMC Breaks
            </Button>

            <Button
              variant="outline"
              size="sm"
              aria-pressed={layers.levels}
              onClick={() => toggleLayer("levels")}
              className={`h-7 px-2.5 text-xs ${
                layers.levels ? "border-slate-300 bg-muted text-foreground" : "text-muted-foreground"
              }`}
              title="Toggle Key Levels (POC, VAH/VAL, S&R)"
            >
              <span className="size-2 rounded-full bg-indigo-500" />
              Levels
            </Button>

            <Button
              variant="outline"
              size="sm"
              aria-pressed={layers.volume}
              onClick={() => toggleLayer("volume")}
              className={`h-7 px-2.5 text-xs ${
                layers.volume ? "border-slate-300 bg-muted text-foreground" : "text-muted-foreground"
              }`}
              title="Toggle Volume Histogram"
            >
              <span className="size-2 rounded-full bg-slate-500" />
              Volume
            </Button>
          </div>
        </CardHeader>
        <CardContent className="pt-1">
          {chartData?.bars && chartData.bars.length > 0 ? (
            <PriceChart
              bars={chartData.bars}
              levels={chartLevels}
              markers={chartMarkers}
              zones={structureZones}
              symbol={underlying}
              interval={chartInterval}
              showZones={layers.fvg || layers.ob}
              showFvg={layers.fvg}
              showOb={layers.ob}
              showMarkers={layers.smc}
              showLevels={layers.levels}
              showVolume={layers.volume}
              showTrendlines={false}
              showClusters={false}
              showEma={layers.ema}
              timezone={timezone}
              height={480}
            />
          ) : (
            <div className="flex h-80 items-center justify-center">
              <Loading rows={4} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Supporting reads beside the AI brief and the trade ticket */}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        {/* Left Column: the book's own reads */}
        <div className="min-w-0 space-y-5">
          {/* Dedicated Fair Value Gaps (FVG) Section */}
          <Card className="border-border/80">
            <CardHeader className="pb-3">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div>
                  <CardTitle className="text-sm font-semibold flex items-center gap-2">
                    <Layers className="size-4 text-purple-600" />
                    Fair Value Gaps (FVG / Imbalances)
                    <Badge variant="outline" className="text-[10px] font-mono border-purple-300 text-purple-700 bg-purple-50">
                      {fvgZones.length} Active Zones
                    </Badge>
                  </CardTitle>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Three-bar liquidity imbalances on 15m. Untested gaps act as high-probability price magnets.
                  </p>
                </div>

                {/* KPI Badges */}
                <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]">
                  <span className="rounded-md border border-purple-200 bg-purple-50/80 px-2 py-0.5 text-purple-800">
                    Fresh: <b>{fvgZones.filter((z) => z.state === "fresh").length}</b>
                  </span>
                  <span className="rounded-md border border-profit/25 bg-profit-soft px-2 py-0.5 text-profit">
                    Bullish: <b>{fvgZones.filter((z) => z.direction === "bull").length}</b>
                  </span>
                  <span className="rounded-md border border-loss/25 bg-loss-soft px-2 py-0.5 text-loss">
                    Bearish: <b>{fvgZones.filter((z) => z.direction === "bear").length}</b>
                  </span>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* Target Nearest Summary Strip */}
              {(nearestFvgAbove || nearestFvgBelow) && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  {nearestFvgAbove && (
                    <div className="flex items-center justify-between rounded-lg border border-loss/25 bg-loss-soft p-2.5 text-xs">
                      <div className="space-y-0.5">
                        <div className="text-[10px] uppercase font-bold text-loss flex items-center gap-1">
                          <TrendingDown className="size-3" /> Nearest Overhead FVG (Resistance)
                        </div>
                        <div className="font-mono font-bold text-foreground">
                          ${num(nearestFvgAbove.low, 1)} – ${num(nearestFvgAbove.high, 1)}
                        </div>
                      </div>
                      <div className="text-right font-mono">
                        <Badge variant="loss" className="text-[10px]">
                          +{Math.abs(nearestFvgAbove.distance_pct || 0).toFixed(2)}% above
                        </Badge>
                        <div className="text-[10px] text-muted-foreground mt-0.5 capitalize">
                          {nearestFvgAbove.state} ({nearestFvgAbove.width ? `$${num(nearestFvgAbove.width, 0)} wide` : ""})
                        </div>
                      </div>
                    </div>
                  )}

                  {nearestFvgBelow && (
                    <div className="flex items-center justify-between rounded-lg border border-profit/25 bg-profit-soft p-2.5 text-xs">
                      <div className="space-y-0.5">
                        <div className="text-[10px] uppercase font-bold text-profit flex items-center gap-1">
                          <TrendingUp className="size-3" /> Nearest Below FVG (Support)
                        </div>
                        <div className="font-mono font-bold text-foreground">
                          ${num(nearestFvgBelow.low, 1)} – ${num(nearestFvgBelow.high, 1)}
                        </div>
                      </div>
                      <div className="text-right font-mono">
                        <Badge variant="profit" className="text-[10px]">
                          -{Math.abs(nearestFvgBelow.distance_pct || 0).toFixed(2)}% below
                        </Badge>
                        <div className="text-[10px] text-muted-foreground mt-0.5 capitalize">
                          {nearestFvgBelow.state} ({nearestFvgBelow.width ? `$${num(nearestFvgBelow.width, 0)} wide` : ""})
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* FVG List */}
              {fvgZones.length > 0 ? (
                <div className="space-y-2 max-h-[320px] overflow-y-auto pr-1">
                  {fvgZones.map((z, idx) => {
                    const isBull = z.direction === "bull";
                    const isFresh = z.state === "fresh";
                    const dist = z.distance_pct ?? 0;
                    const fill = Math.round((z.fill_pct ?? 0) * 100);

                    return (
                      <div
                        key={idx}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 rounded-xl bg-secondary/60 p-2.5 text-xs hover:border-purple-300 transition-colors"
                      >
                        <div className="flex items-center gap-2.5">
                          <Badge
                            variant={isBull ? "profit" : "loss"}
                            className="text-[10px] uppercase font-mono font-bold tracking-wider px-2 py-0.5"
                          >
                            {isBull ? "Bull FVG" : "Bear FVG"}
                          </Badge>

                          <div>
                            <span className="font-mono font-bold text-foreground">
                              ${num(z.low, 1)} – ${num(z.high, 1)}
                            </span>
                            <span className="text-[11px] text-muted-foreground font-mono ml-2">
                              (Width: ${num(z.width ?? (z.high - z.low), 1)})
                            </span>
                          </div>
                        </div>

                        <div className="flex flex-wrap items-center gap-2.5 text-[11px]">
                          {/* Freshness Badge */}
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                              isFresh
                                ? "bg-purple-100 text-purple-800 border border-purple-200"
                                : z.state === "tested"
                                ? "bg-amber-100 text-amber-800 border border-amber-200"
                                : "bg-slate-100 text-slate-700"
                            }`}
                          >
                            {z.state}
                          </span>

                          {/* Fill percentage */}
                          {fill > 0 && (
                            <span className="text-muted-foreground font-mono">
                              {fill}% filled
                            </span>
                          )}

                          {/* Distance */}
                          <span
                            className={`font-mono font-semibold ${
                              dist > 0 ? "text-loss" : dist < 0 ? "text-profit" : "text-slate-600"
                            }`}
                          >
                            {dist > 0 ? `+${dist.toFixed(2)}%` : `${dist.toFixed(2)}%`}
                          </span>

                          {/* Time */}
                          <span className="text-muted-foreground font-mono">
                            {ago(z.created_at)}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="py-6 text-center text-xs text-muted-foreground">
                  No active Fair Value Gaps found in current lookback window.
                </div>
              )}
            </CardContent>
          </Card>

          {/* PCR & 24h Options Book History Chart */}
          <PcrHistoryChart underlying={underlying} timezone={timezone} />
        </div>

        {/* Right Column: the read, then the ticket it informs */}
        <div className="space-y-4">
          <AiBriefCard underlying={underlying} kind="market" />
          <DirectPerpPanel
            underlying={underlying}
            currentPrice={currentPrice}
            structure={structure}
          />
        </div>
      </div>

      {/* Structure: the trendline chart and its levels, full width — this is the page's widest read */}
      <div className="space-y-5">
        <TrendlineStructureChart
          bars={chartData?.bars || []}
          trendlines={trendlinesData?.trendlines || []}
          clusters={trendlinesData?.horizontal_clusters || []}
          symbol={underlying}
          interval={chartInterval}
          currentPrice={currentPrice}
          timezone={timezone}
          onIntervalChange={setChartInterval}
          height={460}
        />

        <TrendlinesTable
          trendlines={trendlinesData?.trendlines || []}
          clusters={trendlinesData?.horizontal_clusters || []}
          currentPrice={currentPrice}
          symbol={underlying}
          interval={chartInterval}
          showTrendlinesOnChart={true}
          showClustersOnChart={true}
          hiddenIds={hiddenItemIds}
          onToggleItem={toggleItemVisibility}
        />

        <FibonacciCard fib={fibData} currentPrice={currentPrice} />
      </div>

      {/* Comprehensive Analysis Tabs */}
      <Tabs defaultValue="outlook" className="space-y-4">
        <TabsList className="grid w-full grid-cols-4 sm:w-auto">
          <TabsTrigger value="outlook" className="gap-2 text-xs">
            <Compass className="size-3.5" /> Outlook
          </TabsTrigger>
          <TabsTrigger value="smc" className="gap-2 text-xs">
            <Activity className="size-3.5" /> CHoCH / BOS & SMC
          </TabsTrigger>
          <TabsTrigger value="structure" className="gap-2 text-xs">
            <Layers className="size-3.5" /> Structure & Levels
          </TabsTrigger>
          <TabsTrigger value="verdicts" className="gap-2 text-xs">
            <Zap className="size-3.5" /> Verdicts ({verdicts?.verdicts?.length ?? 0})
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
                      isUp ? "bg-profit" : isDown ? "bg-loss" : "bg-slate-300"
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

        {/* SMC / CHoCH & BOS Tab */}
        <TabsContent value="smc" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            {/* Recent CHoCH & BOS Swings */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Activity className="size-4 text-primary" /> Market Structure Breaks (CHoCH / BOS)
                </CardTitle>
                <p className="text-xs text-muted-foreground">
                  Change of Character (trend shifts) and Break of Structure (trend continuations) on 15m bars.
                </p>
              </CardHeader>
              <CardContent>
                {swings && swings.length > 0 ? (
                  <div className="space-y-2">
                    {swings.map((sw, i) => {
                      const isUp = sw.direction === "up";
                      return (
                        <div
                          key={i}
                          className="flex items-center justify-between rounded-xl bg-secondary/60 px-3 py-2 text-xs"
                        >
                          <div className="flex items-center gap-2.5">
                            <Badge variant={sw.kind === "choch" ? "primary" : "default"} className="num uppercase">
                              {sw.kind}
                            </Badge>
                            <span className={`font-semibold ${isUp ? "text-profit" : "text-loss"}`}>
                              {sw.direction.toUpperCase()}
                            </span>
                            <span className="font-mono font-bold">${num(sw.level, 1)}</span>
                          </div>

                          <div className="flex items-center gap-3 font-mono text-[11px] text-muted-foreground">
                            <span>Displ: {sw.displacement.toFixed(1)}x ATR</span>
                            <span>Vol: {sw.volume_ratio.toFixed(1)}x</span>
                            <span>{ago(sw.bar_time)}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="py-6 text-center text-xs text-muted-foreground">
                    No structural breaks identified in the recent window.
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Fair Value Gaps (FVG) */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Layers className="size-4 text-primary" /> Fair Value Gaps (FVG / Imbalances)
                </CardTitle>
                <p className="text-xs text-muted-foreground">
                  Unmitigated single-candle price inefficiencies acting as market magnets.
                </p>
              </CardHeader>
              <CardContent>
                {gaps && gaps.length > 0 ? (
                  <div className="space-y-2">
                    {gaps.map((g, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between rounded-xl bg-secondary/60 px-3 py-2 text-xs"
                      >
                        <div className="flex items-center gap-2.5">
                          <Badge variant={g.direction === "up" ? "profit" : "loss"} className="text-[10px] uppercase font-mono">
                            {g.direction} gap
                          </Badge>
                          <span className="font-mono font-semibold">
                            ${num(g.low, 1)} – ${num(g.high, 1)}
                          </span>
                        </div>

                        <div className="flex items-center gap-2 text-[11px]">
                          <Badge variant={g.filled ? "default" : "outline"} className="text-[10px]">
                            {g.filled ? "Filled" : "Unfilled Active"}
                          </Badge>
                          <span className="text-muted-foreground font-mono">{ago(g.bar_time)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="py-6 text-center text-xs text-muted-foreground">
                    No active fair value gaps detected on current candles.
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* Structure & Levels Tab */}
        <TabsContent value="structure" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            {/* Resistance Levels */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold text-loss flex items-center gap-2">
                  <ArrowUpRight className="size-4" /> Levels & Resistances Above
                </CardTitle>
              </CardHeader>
              <CardContent>
                {structure?.nearby?.above && structure.nearby.above.length > 0 ? (
                  <div className="space-y-2">
                    {structure.nearby.above.map((lvl, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between rounded-lg border border-loss/25 bg-loss-soft px-3 py-2 text-xs"
                      >
                        <div>
                          <span className="font-mono font-bold text-loss">${num(lvl.price, 2)}</span>
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
                <CardTitle className="text-sm font-semibold text-profit flex items-center gap-2">
                  <ArrowDownRight className="size-4" /> Levels & Supports Below
                </CardTitle>
              </CardHeader>
              <CardContent>
                {structure?.nearby?.below && structure.nearby.below.length > 0 ? (
                  <div className="space-y-2">
                    {structure.nearby.below.map((lvl, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between rounded-lg border border-profit/25 bg-profit-soft px-3 py-2 text-xs"
                      >
                        <div>
                          <span className="font-mono font-bold text-profit">${num(lvl.price, 2)}</span>
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
      </Tabs>
    </div>
  );
}
