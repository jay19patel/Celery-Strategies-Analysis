"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  LineSeries,
  LineStyle,
  AreaSeries,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import {
  ArrowDownRight,
  ArrowUpRight,
  Compass,
  Eye,
  EyeOff,
  Maximize2,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { num, signed, usd } from "@/lib/format";
import type { Bar, HorizontalClusterItem, TrendlineItem } from "@/lib/types";

interface TrendlineStructureChartProps {
  bars: Bar[];
  trendlines: TrendlineItem[];
  clusters: HorizontalClusterItem[];
  symbol?: string;
  interval?: string;
  currentPrice?: number;
  timezone?: "Local" | "UTC" | "IST";
  height?: number;
  onIntervalChange?: (interval: "15m" | "1h" | "1d") => void;
  className?: string;
}

/**
 * Dedicated Simple Line Structure Chart for Trendlines and Multi-Swing Cross-Levels.
 * - Simple close-price line chart (NO candlestick mixture).
 * - Rock-solid zoom-invariant rendering (Lightweight-charts native LineSeries & PriceLines).
 * - Trendlines and levels NEVER warp, jump, or move when zooming in or out.
 */
export function TrendlineStructureChart({
  bars,
  trendlines,
  clusters,
  symbol = "BTC",
  interval = "15m",
  currentPrice = 0,
  timezone = "IST",
  height = 360,
  onIntervalChange,
  className,
}: TrendlineStructureChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const mainLineSeriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const trendlineSeriesRefs = useRef<ISeriesApi<"Line">[]>([]);
  const priceLineRefs = useRef<IPriceLine[]>([]);

  // Bumped every time the chart is rebuilt (a height or timezone change). Everything that fills
  // the chart depends on it, so a rebuild is always followed by a refill — without it the new
  // chart stays empty of its line, trendlines and levels until their own data happens to change.
  const [built, setBuilt] = useState(0);

  // Toggles for the dedicated chart
  const [showSupportRays, setShowSupportRays] = useState(true);
  const [showResistanceRays, setShowResistanceRays] = useState(true);
  const [showCrossLevels, setShowCrossLevels] = useState(true);
  const [hoverData, setHoverData] = useState<{ time?: string; price?: number } | null>(null);

  // Stats
  const supportCount = useMemo(() => trendlines.filter((t) => t.kind === "support").length, [trendlines]);
  const resistanceCount = useMemo(() => trendlines.filter((t) => t.kind === "resistance").length, [trendlines]);

  const nearestSupport = useMemo(() => {
    const list = trendlines.filter((t) => t.kind === "support" && t.distance_pct <= 0);
    return list.length ? list.reduce((prev, curr) => (curr.distance_pct > prev.distance_pct ? curr : prev)) : null;
  }, [trendlines]);

  const nearestResistance = useMemo(() => {
    const list = trendlines.filter((t) => t.kind === "resistance" && t.distance_pct >= 0);
    return list.length ? list.reduce((prev, curr) => (curr.distance_pct < prev.distance_pct ? curr : prev)) : null;
  }, [trendlines]);

  // Clean data sorted by time
  const sortedBars = useMemo(() => {
    const list = [...bars].sort((a, b) => a.time - b.time);
    const seen = new Set<number>();
    const out: Bar[] = [];
    for (const b of list) {
      if (!seen.has(b.time)) {
        seen.add(b.time);
        out.push(b);
      }
    }
    return out;
  }, [bars]);

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#64748b",
        fontSize: 11,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#f1f5f9" },
        horzLines: { color: "#f1f5f9" },
      },
      rightPriceScale: {
        borderColor: "#e2e8f0",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: "#e2e8f0",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (t: Time) => {
          const d = new Date((t as number) * 1000);
          if (timezone === "IST") {
            const ist = new Date(d.getTime() + 5.5 * 3600 * 1000);
            return `${String(ist.getUTCHours()).padStart(2, "0")}:${String(ist.getUTCMinutes()).padStart(2, "0")}`;
          }
          if (timezone === "UTC") {
            return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
          }
          return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
        },
      },
      crosshair: {
        vertLine: { color: "#94a3b8", width: 1, style: LineStyle.Dashed },
        horzLine: { color: "#94a3b8", width: 1, style: LineStyle.Dashed },
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
    });
    chartRef.current = chart;
    setBuilt((n) => n + 1);  // the chart is empty: every effect that fills it now runs again

    // Clean close price Area/Line series (NO candles)
    const area = chart.addSeries(AreaSeries, {
      lineColor: "#2563eb",
      topColor: "rgba(37, 99, 235, 0.14)",
      bottomColor: "rgba(37, 99, 235, 0.01)",
      lineWidth: 2,
      priceLineVisible: true,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
    });
    mainLineSeriesRef.current = area;

    // Crosshair hover listener
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData.get(area)) {
        setHoverData(null);
        return;
      }
      const data = param.seriesData.get(area) as { value?: number } | undefined;
      const d = new Date((param.time as number) * 1000);
      const timeStr = `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      setHoverData({ time: timeStr, price: data?.value });
    });

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      // Every series and price line belonged to the chart just removed. Held on to, the next mount
      // would hand the new chart handles into the old one and lightweight-charts would throw.
      mainLineSeriesRef.current = null;
      trendlineSeriesRefs.current = [];
      priceLineRefs.current = [];
    };
  }, [height, timezone]);

  // Set line chart data
  useEffect(() => {
    if (!mainLineSeriesRef.current || !sortedBars.length) return;
    const lineData = sortedBars.map((b) => ({
      time: b.time as UTCTimestamp,
      value: b.close,
    }));
    mainLineSeriesRef.current.setData(lineData);
  }, [built, sortedBars]);

  // Render native Trendlines on the chart (rock-solid, 100% zoom-invariant)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !sortedBars.length) return;

    // Clean up previous trendline series
    for (const s of trendlineSeriesRefs.current) {
      chart.removeSeries(s);
    }
    trendlineSeriesRefs.current = [];

    const latestBar = sortedBars[sortedBars.length - 1];
    const latestTime = latestBar.time;

    // Filter which trendlines to draw
    const activeLines = trendlines.filter((t) => {
      if (t.kind === "support" && !showSupportRays) return false;
      if (t.kind === "resistance" && !showResistanceRays) return false;
      return true;
    });

    for (const tl of activeLines) {
      const isSupport = tl.kind === "support";
      const strokeColor = isSupport ? "#2563eb" : "#e11d48";

      // 1. Anchor segment (between start_time and end_time)
      const anchorSeries = chart.addSeries(LineSeries, {
        color: strokeColor,
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });

      const anchorPoints = [
        { time: tl.start_time as UTCTimestamp, value: tl.start_price },
        { time: tl.end_time as UTCTimestamp, value: tl.end_price },
      ].sort((a, b) => (a.time as number) - (b.time as number));

      anchorSeries.setData(anchorPoints);
      trendlineSeriesRefs.current.push(anchorSeries);

      // 2. Projected forward ray segment (from end_time to latest candle)
      if (latestTime > tl.end_time) {
        const raySeries = chart.addSeries(LineSeries, {
          color: strokeColor,
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });

        const rayPoints = [
          { time: tl.end_time as UTCTimestamp, value: tl.end_price },
          { time: latestTime as UTCTimestamp, value: tl.current_price },
        ];

        raySeries.setData(rayPoints);
        trendlineSeriesRefs.current.push(raySeries);
      }
    }
  }, [built, showResistanceRays, showSupportRays, sortedBars, trendlines]);

  // Render native Horizontal Multi-Swing Cross-Levels (rock-solid PriceLines)
  useEffect(() => {
    const area = mainLineSeriesRef.current;
    if (!area) return;

    // Clean up previous price lines
    for (const pl of priceLineRefs.current) {
      area.removePriceLine(pl);
    }
    priceLineRefs.current = [];

    if (!showCrossLevels) return;

    for (const c of clusters) {
      const isSupport = c.kind.includes("bottom") || c.kind.includes("support");
      const strokeColor = isSupport ? "#10b981" : "#ef4444";
      const formattedKind = c.kind
        .split("_")
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(" ");

      const pl = area.createPriceLine({
        price: c.price,
        color: strokeColor,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${formattedKind} (${c.touches}x)`,
      });

      priceLineRefs.current.push(pl);
    }
  }, [built, clusters, showCrossLevels]);

  const handleResetZoom = () => {
    chartRef.current?.timeScale().fitContent();
  };

  const displayPrice = hoverData?.price ?? (sortedBars.length ? sortedBars[sortedBars.length - 1].close : currentPrice);

  return (
    <Card className={`border-border/80 shadow-xs ${className ?? ""}`}>
      <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between pb-2">
        <div className="flex flex-wrap items-center gap-3">
          <div className="space-y-0.5">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <TrendingUp className="size-4 text-blue-600" />
              {symbol} Trendlines & Cross-Levels Structural Chart ({interval})
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              Pure clean line chart with mathematically invariant diagonal rays and multi-swing price clusters. Zoom in/out without distortion.
            </p>
          </div>

          {/* Timeframe Switcher */}
          {onIntervalChange && (
            <div className="flex items-center rounded-lg border bg-muted/40 p-1">
              {(["15m", "1h", "1d"] as const).map((tf) => (
                <button
                  key={tf}
                  type="button"
                  onClick={() => onIntervalChange(tf)}
                  className={`rounded-md px-2.5 py-0.5 font-mono text-xs font-semibold transition-all ${
                    interval === tf
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Clean Layer Toggles */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            variant={showSupportRays ? "default" : "outline"}
            size="sm"
            onClick={() => setShowSupportRays((v) => !v)}
            className={`h-7 px-2.5 text-xs font-mono transition-all ${
              showSupportRays
                ? "bg-blue-600 text-white hover:bg-blue-700 shadow-xs"
                : "text-muted-foreground opacity-60 hover:opacity-100"
            }`}
            title="Toggle Support Trendlines on Line Chart"
          >
            <span className="size-2 rounded-full mr-1.5 bg-blue-300" />
            Support ({supportCount})
          </Button>

          <Button
            variant={showResistanceRays ? "default" : "outline"}
            size="sm"
            onClick={() => setShowResistanceRays((v) => !v)}
            className={`h-7 px-2.5 text-xs font-mono transition-all ${
              showResistanceRays
                ? "bg-rose-600 text-white hover:bg-rose-700 shadow-xs"
                : "text-muted-foreground opacity-60 hover:opacity-100"
            }`}
            title="Toggle Resistance Trendlines on Line Chart"
          >
            <span className="size-2 rounded-full mr-1.5 bg-rose-300" />
            Resistance ({resistanceCount})
          </Button>

          <Button
            variant={showCrossLevels ? "default" : "outline"}
            size="sm"
            onClick={() => setShowCrossLevels((v) => !v)}
            className={`h-7 px-2.5 text-xs font-mono transition-all ${
              showCrossLevels
                ? "bg-amber-600 text-white hover:bg-amber-700 shadow-xs"
                : "text-muted-foreground opacity-60 hover:opacity-100"
            }`}
            title="Toggle Multi-Swing Cross Levels on Line Chart"
          >
            <span className="size-2 rounded-full mr-1.5 bg-amber-300" />
            Cross Levels ({clusters.length})
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={handleResetZoom}
            className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
            title="Reset Zoom / Fit Content"
          >
            <Maximize2 className="size-3" />
          </Button>
        </div>
      </CardHeader>

      <CardContent className="pt-0">
        {/* Floating Mini HUD */}
        <div className="mb-2 flex flex-wrap items-center justify-between rounded-lg border bg-muted/20 px-3 py-1.5 text-xs font-mono">
          <div className="flex items-center gap-3">
            <span className="font-bold text-foreground">
              {symbol} Close: <b className="text-primary">${displayPrice ? usd(displayPrice, 1) : "—"}</b>
            </span>
            {hoverData?.time && (
              <span className="text-muted-foreground text-[11px]">
                Bar: {hoverData.time}
              </span>
            )}
          </div>

          <div className="flex items-center gap-4 text-[11px]">
            {nearestSupport && (
              <span className="text-blue-600">
                Sup: <b>${usd(nearestSupport.current_price, 1)}</b> ({signed(nearestSupport.distance_pct, 2)}%)
              </span>
            )}
            {nearestResistance && (
              <span className="text-rose-600">
                Res: <b>${usd(nearestResistance.current_price, 1)}</b> ({signed(nearestResistance.distance_pct, 2)}%)
              </span>
            )}
          </div>
        </div>

        {/* The Clean Chart Container */}
        <div ref={chartContainerRef} className="relative w-full rounded-lg border bg-background" style={{ height }} />
      </CardContent>
    </Card>
  );
}
