"use client";

import { useMemo, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  Compass,
  Eye,
  EyeOff,
  Layers,
  LineChart,
  Minus,
  Sparkles,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { num, signed, usd } from "@/lib/format";
import type { HorizontalClusterItem, TrendlineItem } from "@/lib/types";

interface TrendlinesTableProps {
  trendlines: TrendlineItem[];
  clusters: HorizontalClusterItem[];
  currentPrice: number;
  symbol: string;
  interval: string;
  showTrendlinesOnChart?: boolean;
  showClustersOnChart?: boolean;
  onToggleTrendlines?: () => void;
  onToggleClusters?: () => void;
  hiddenIds?: Set<string>;
  onToggleItem?: (id: string) => void;
  className?: string;
}

/**
 * Mini SVG visual trajectory diagram showing line slope and touch points.
 */
function MiniTrendlineSvg({
  kind,
  touches,
  slope,
}: {
  kind: "support" | "resistance";
  touches: number;
  slope: number;
}) {
  const isSupport = kind === "support";
  const strokeColor = isSupport ? "#3b82f6" : "#f43f5e";
  const fillColor = isSupport ? "#93c5fd" : "#fda4af";

  // Coordinates for 90x28 viewBox
  const y1 = isSupport ? 22 : 6;
  const y2 = isSupport ? 6 : 22;

  // Distribute touch points along line
  const count = Math.max(2, Math.min(touches, 6));
  const points = [];
  for (let i = 0; i < count; i++) {
    const t = i / (count - 1);
    const x = 10 + t * 70;
    const y = y1 + t * (y2 - y1);
    points.push({ x, y });
  }

  return (
    <div className="flex items-center gap-1.5" title={`${touches} verified touches`}>
      <svg width="84" height="26" viewBox="0 0 90 28" className="overflow-visible">
        {/* Background guideline */}
        <line x1="4" y1={isSupport ? 25 : 3} x2="86" y2={isSupport ? 25 : 3} stroke="#e2e8f0" strokeWidth="1" strokeDasharray="2,2" />
        {/* Sloped Trendline */}
        <line
          x1="8"
          y1={y1}
          x2="82"
          y2={y2}
          stroke={strokeColor}
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        {/* Forward Projected Ray (dashed) */}
        <line
          x1="82"
          y1={y2}
          x2="92"
          y2={y2 + (y2 - y1) * 0.15}
          stroke={strokeColor}
          strokeWidth="1.5"
          strokeDasharray="2,2"
        />
        {/* Touch Circles */}
        {points.map((pt, idx) => (
          <circle
            key={idx}
            cx={pt.x}
            cy={pt.y}
            r="3"
            fill={fillColor}
            stroke={strokeColor}
            strokeWidth="1.5"
          />
        ))}
      </svg>
    </div>
  );
}

/**
 * Mini SVG horizontal bounce line showing multiple swing high/low touches.
 */
function MiniClusterSvg({
  kind,
  touches,
}: {
  kind: string;
  touches: number;
}) {
  const isBottom = kind.includes("bottom") || kind.includes("support");
  const strokeColor = isBottom ? "#10b981" : "#ef4444";
  const circleColor = isBottom ? "#34d399" : "#f87171";

  // Coordinates for 84x24 viewBox
  const lineY = isBottom ? 18 : 6;
  const count = Math.max(2, Math.min(touches, 6));

  const touchPoints = [];
  for (let i = 0; i < count; i++) {
    const t = (i + 0.5) / count;
    const x = 12 + t * 60;
    touchPoints.push(x);
  }

  return (
    <div className="flex items-center gap-1.5" title={`${touches} swing bounce touches`}>
      <svg width="84" height="24" viewBox="0 0 84 24" className="overflow-visible">
        {/* Horizontal Level */}
        <line
          x1="4"
          y1={lineY}
          x2="80"
          y2={lineY}
          stroke={strokeColor}
          strokeWidth="2"
          strokeDasharray="3,2"
        />
        {/* Bounce V-shapes or Inverted-V shapes */}
        {touchPoints.map((x, idx) => (
          <g key={idx}>
            <circle
              cx={x}
              cy={lineY}
              r="2.75"
              fill={circleColor}
              stroke={strokeColor}
              strokeWidth="1.5"
            />
            {/* Bounce arrow/tick */}
            <line
              x1={x}
              y1={isBottom ? lineY - 6 : lineY + 6}
              x2={x}
              y2={lineY}
              stroke={strokeColor}
              strokeWidth="1.2"
              opacity="0.6"
            />
          </g>
        ))}
      </svg>
    </div>
  );
}

export function TrendlinesTable({
  trendlines,
  clusters,
  currentPrice,
  symbol,
  interval,
  showTrendlinesOnChart = true,
  showClustersOnChart = true,
  onToggleTrendlines,
  onToggleClusters,
  hiddenIds = new Set<string>(),
  onToggleItem,
  className,
}: TrendlinesTableProps) {
  const [activeTab, setActiveTab] = useState<"trendlines" | "clusters">("trendlines");

  const supportCount = useMemo(() => trendlines.filter((t) => t.kind === "support").length, [trendlines]);
  const resistanceCount = useMemo(() => trendlines.filter((t) => t.kind === "resistance").length, [trendlines]);

  // Nearest support trendline below
  const nearestSupport = useMemo(() => {
    const list = trendlines.filter((t) => t.kind === "support" && t.distance_pct <= 0);
    return list.length ? list.reduce((prev, curr) => (curr.distance_pct > prev.distance_pct ? curr : prev)) : null;
  }, [trendlines]);

  // Nearest resistance trendline above
  const nearestResistance = useMemo(() => {
    const list = trendlines.filter((t) => t.kind === "resistance" && t.distance_pct >= 0);
    return list.length ? list.reduce((prev, curr) => (curr.distance_pct < prev.distance_pct ? curr : prev)) : null;
  }, [trendlines]);

  return (
    <Card className={`border-border/80 shadow-xs ${className ?? ""}`}>
      <CardHeader className="pb-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <TrendingUp className="size-4 text-blue-600" />
              Trendlines & Multi-Swing Cross Levels ({interval})
              <Badge variant="outline" className="text-[10px] font-mono border-blue-300 text-blue-700 bg-blue-50">
                {trendlines.length} Rays • {clusters.length} Horizontal Levels
              </Badge>
            </CardTitle>
            <p className="text-xs text-muted-foreground mt-0.5">
              Pure algorithmic pivot swings with validated touch anchors, projected forward rays, and multi-swing price clusters. Click eye icons to toggle on chart.
            </p>
          </div>

          {/* Master Enable/Disable Chart Toggles */}
          <div className="flex flex-wrap items-center gap-2">
            {onToggleTrendlines && (
              <Button
                variant={showTrendlinesOnChart ? "default" : "outline"}
                size="sm"
                onClick={onToggleTrendlines}
                className={`h-7 px-2.5 text-xs font-mono transition-all ${
                  showTrendlinesOnChart
                    ? "bg-blue-600 text-white hover:bg-blue-700 shadow-xs"
                    : "text-muted-foreground opacity-60 hover:opacity-100"
                }`}
                title="Enable / Disable Trendlines on Candlestick Chart"
              >
                {showTrendlinesOnChart ? <Eye className="size-3.5 mr-1.5" /> : <EyeOff className="size-3.5 mr-1.5" />}
                Trendlines ({trendlines.length})
              </Button>
            )}

            {onToggleClusters && (
              <Button
                variant={showClustersOnChart ? "default" : "outline"}
                size="sm"
                onClick={onToggleClusters}
                className={`h-7 px-2.5 text-xs font-mono transition-all ${
                  showClustersOnChart
                    ? "bg-amber-600 text-white hover:bg-amber-700 shadow-xs"
                    : "text-muted-foreground opacity-60 hover:opacity-100"
                }`}
                title="Enable / Disable Multi-Swing Cross Levels on Candlestick Chart"
              >
                {showClustersOnChart ? <Eye className="size-3.5 mr-1.5" /> : <EyeOff className="size-3.5 mr-1.5" />}
                Cross Levels ({clusters.length})
              </Button>
            )}
          </div>
        </div>

        {/* Nearest Structural Levels Strip */}
        <div className="mt-2.5 grid grid-cols-1 sm:grid-cols-2 gap-2 rounded-lg border bg-muted/20 p-2 text-xs">
          <div className="flex items-center justify-between px-1">
            <span className="text-muted-foreground flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-blue-500" />
              Nearest Trendline Support:
            </span>
            {nearestSupport ? (
              <span className="font-mono font-semibold text-blue-600">
                ${usd(nearestSupport.current_price, 1)}{" "}
                <span className="text-[11px] font-normal text-muted-foreground">
                  ({signed(nearestSupport.distance_pct, 2)}%)
                </span>
              </span>
            ) : (
              <span className="font-mono text-muted-foreground">—</span>
            )}
          </div>

          <div className="flex items-center justify-between px-1 border-t sm:border-t-0 sm:border-l pt-1 sm:pt-0 sm:pl-3">
            <span className="text-muted-foreground flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-rose-500" />
              Nearest Trendline Resistance:
            </span>
            {nearestResistance ? (
              <span className="font-mono font-semibold text-rose-600">
                ${usd(nearestResistance.current_price, 1)}{" "}
                <span className="text-[11px] font-normal text-muted-foreground">
                  ({signed(nearestResistance.distance_pct, 2)}%)
                </span>
              </span>
            ) : (
              <span className="font-mono text-muted-foreground">—</span>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="pt-0">
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as "trendlines" | "clusters")}>
          <TabsList className="grid w-full grid-cols-2 sm:w-auto mb-3">
            <TabsTrigger value="trendlines" className="gap-2 text-xs">
              <LineChart className="size-3.5 text-blue-600" />
              Trendline Rays ({trendlines.length})
            </TabsTrigger>
            <TabsTrigger value="clusters" className="gap-2 text-xs">
              <Layers className="size-3.5 text-amber-600" />
              Multi-Swing Cross Levels ({clusters.length})
            </TabsTrigger>
          </TabsList>

          {/* Tab 1: Trendline Rays */}
          <TabsContent value="trendlines" className="m-0 space-y-2">
            {trendlines.length > 0 ? (
              <div className="overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-muted/40 text-[11px]">
                      <TableHead className="w-12 text-center" title="Toggle visible on chart">Chart</TableHead>
                      <TableHead className="w-36">Type & Direction</TableHead>
                      <TableHead className="w-24">Touches</TableHead>
                      <TableHead>Anchor Range (Start → End)</TableHead>
                      <TableHead className="text-right">Projected Price</TableHead>
                      <TableHead className="text-right">Distance to Mark</TableHead>
                      <TableHead className="w-28 text-center">Trajectory</TableHead>
                      <TableHead className="w-24 text-right">Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {trendlines.map((tl) => {
                      const isSupport = tl.kind === "support";
                      const isClose = Math.abs(tl.distance_pct) < 0.6;
                      const isHidden = hiddenIds.has(tl.id) || !showTrendlinesOnChart;

                      return (
                        <TableRow
                          key={tl.id}
                          className={`text-xs transition-opacity hover:bg-muted/30 ${
                            isHidden ? "opacity-50" : ""
                          }`}
                        >
                          <TableCell className="text-center">
                            {onToggleItem ? (
                              <button
                                type="button"
                                onClick={() => onToggleItem(tl.id)}
                                title={isHidden ? "Click to show on chart" : "Click to hide from chart"}
                                className={`inline-flex items-center justify-center size-6 rounded transition-all ${
                                  isHidden
                                    ? "text-muted-foreground/40 hover:text-foreground hover:bg-muted"
                                    : "text-blue-600 hover:bg-blue-50"
                                }`}
                              >
                                {isHidden ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                              </button>
                            ) : (
                              <Eye className="size-3.5 mx-auto text-blue-600" />
                            )}
                          </TableCell>

                          <TableCell>
                            <div className="flex items-center gap-1.5">
                              <Badge
                                variant={isSupport ? "default" : "outline"}
                                className={`text-[10px] font-medium ${
                                  isSupport
                                    ? "bg-blue-600 hover:bg-blue-700 text-white"
                                    : "border-rose-400 text-rose-700 bg-rose-50"
                                }`}
                              >
                                {isSupport ? <ArrowUpRight className="size-3 mr-1" /> : <ArrowDownRight className="size-3 mr-1" />}
                                {isSupport ? "Support Ray" : "Resistance Ray"}
                              </Badge>
                            </div>
                          </TableCell>

                          <TableCell>
                            <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 font-mono text-[11px] font-semibold">
                              <span className="size-1.5 rounded-full bg-foreground/60" />
                              {tl.touches} touches
                            </span>
                          </TableCell>

                          <TableCell className="font-mono text-[11px] text-muted-foreground">
                            ${num(tl.start_price, 1)}{" "}
                            <span className="mx-1 text-slate-400">→</span>{" "}
                            ${num(tl.end_price, 1)}
                          </TableCell>

                          <TableCell className="font-mono text-right font-bold text-foreground">
                            ${usd(tl.current_price, 1)}
                          </TableCell>

                          <TableCell className="font-mono text-right">
                            <span
                              className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                                isClose
                                  ? "bg-amber-100 text-amber-800 border border-amber-300"
                                  : tl.distance_pct >= 0
                                  ? "text-rose-600"
                                  : "text-emerald-600"
                              }`}
                            >
                              {signed(tl.distance_pct, 2)}%
                            </span>
                          </TableCell>

                          <TableCell className="text-center">
                            <div className="flex justify-center">
                              <MiniTrendlineSvg
                                kind={tl.kind}
                                touches={tl.touches}
                                slope={tl.slope}
                              />
                            </div>
                          </TableCell>

                          <TableCell className="text-right">
                            <Badge
                              variant={tl.status === "approaching" ? "solid" : "outline"}
                              className={`text-[9px] uppercase px-1.5 py-0 ${
                                isHidden
                                  ? "border-slate-300 text-slate-400 bg-slate-50"
                                  : tl.status === "approaching"
                                  ? "bg-amber-500 text-white"
                                  : tl.status === "broken"
                                  ? "border-slate-300 text-slate-500"
                                  : "border-emerald-300 text-emerald-700 bg-emerald-50"
                              }`}
                            >
                              {isHidden ? "Hidden" : tl.status}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <div className="rounded-lg border border-dashed py-8 text-center text-xs text-muted-foreground">
                No active trendlines detected in current range. They will automatically formulate when pivot swings align.
              </div>
            )}
          </TabsContent>

          {/* Tab 2: Multi-Swing Horizontal Cross-Levels */}
          <TabsContent value="clusters" className="m-0 space-y-2">
            {clusters.length > 0 ? (
              <div className="overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-muted/40 text-[11px]">
                      <TableHead className="w-12 text-center" title="Toggle visible on chart">Chart</TableHead>
                      <TableHead className="w-44">Structural Cross Level</TableHead>
                      <TableHead className="w-24">Swings</TableHead>
                      <TableHead className="text-right">Price Level</TableHead>
                      <TableHead className="text-right">Distance to Mark</TableHead>
                      <TableHead className="w-28 text-center">Bounce Pattern</TableHead>
                      <TableHead className="w-24 text-right">Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {clusters.map((c) => {
                      const isSupport = c.kind.includes("bottom") || c.kind.includes("support");
                      const isClose = Math.abs(c.distance_pct) < 0.5;
                      const isHidden = hiddenIds.has(c.id) || !showClustersOnChart;
                      const formattedKind = c.kind
                        .split("_")
                        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
                        .join(" ");

                      return (
                        <TableRow
                          key={c.id}
                          className={`text-xs transition-opacity hover:bg-muted/30 ${
                            isHidden ? "opacity-50" : ""
                          }`}
                        >
                          <TableCell className="text-center">
                            {onToggleItem ? (
                              <button
                                type="button"
                                onClick={() => onToggleItem(c.id)}
                                title={isHidden ? "Click to show on chart" : "Click to hide from chart"}
                                className={`inline-flex items-center justify-center size-6 rounded transition-all ${
                                  isHidden
                                    ? "text-muted-foreground/40 hover:text-foreground hover:bg-muted"
                                    : "text-amber-600 hover:bg-amber-50"
                                }`}
                              >
                                {isHidden ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                              </button>
                            ) : (
                              <Eye className="size-3.5 mx-auto text-amber-600" />
                            )}
                          </TableCell>

                          <TableCell>
                            <Badge
                              variant="outline"
                              className={`text-[10px] font-medium ${
                                isSupport
                                  ? "border-emerald-300 text-emerald-800 bg-emerald-50"
                                  : "border-rose-300 text-rose-800 bg-rose-50"
                              }`}
                            >
                              {formattedKind}
                            </Badge>
                          </TableCell>

                          <TableCell>
                            <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 font-mono text-[11px] font-semibold">
                              <span className="size-1.5 rounded-full bg-foreground/60" />
                              {c.touches} pivots
                            </span>
                          </TableCell>

                          <TableCell className="font-mono text-right font-bold text-foreground">
                            ${usd(c.price, 1)}
                          </TableCell>

                          <TableCell className="font-mono text-right">
                            <span
                              className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                                isClose
                                  ? "bg-amber-100 text-amber-800 border border-amber-300"
                                  : c.distance_pct >= 0
                                  ? "text-rose-600"
                                  : "text-emerald-600"
                              }`}
                            >
                              {signed(c.distance_pct, 2)}%
                            </span>
                          </TableCell>

                          <TableCell className="text-center">
                            <div className="flex justify-center">
                              <MiniClusterSvg kind={c.kind} touches={c.touches} />
                            </div>
                          </TableCell>

                          <TableCell className="text-right">
                            <Badge
                              variant={c.status === "approaching" ? "solid" : "outline"}
                              className={`text-[9px] uppercase px-1.5 py-0 ${
                                isHidden
                                  ? "border-slate-300 text-slate-400 bg-slate-50"
                                  : c.status === "approaching"
                                  ? "bg-amber-500 text-white"
                                  : "border-emerald-300 text-emerald-700 bg-emerald-50"
                              }`}
                            >
                              {isHidden ? "Hidden" : c.status}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <div className="rounded-lg border border-dashed py-8 text-center text-xs text-muted-foreground">
                No multi-swing horizontal cross levels clustered in this timeframe.
              </div>
            )}
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
