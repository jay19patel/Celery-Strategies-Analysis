"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { ChevronDown, ChevronUp } from "lucide-react";

import type { Bar, HorizontalClusterItem, StructureZone, TrendlineItem } from "@/lib/types";

export interface Level {
  price: number;
  label: string;
  color: string;
  dashed?: boolean;
}

export interface ChartMarker {
  time: number;
  kind: "choch" | "bos";
  direction: "up" | "down";
  level?: number;
}

function formatChartTime(ts: number, timezone: "Local" | "UTC" | "IST"): string {
  const tz = timezone === "IST" ? "Asia/Kolkata" : timezone === "UTC" ? "UTC" : undefined;
  const d = new Date(ts * 1000);
  return d.toLocaleString("en-GB", {
    timeZone: tz,
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function calcEma(bars: Bar[], period: number): Array<{ time: UTCTimestamp; value: number }> {
  if (bars.length < period) return [];
  const sorted = [...bars].sort((a, b) => a.time - b.time);
  const k = 2 / (period + 1);
  const res: Array<{ time: UTCTimestamp; value: number }> = [];
  let ema = sorted.slice(0, period).reduce((acc, b) => acc + b.close, 0) / period;
  res.push({ time: sorted[period - 1].time as UTCTimestamp, value: Math.round(ema * 10) / 10 });
  for (let i = period; i < sorted.length; i++) {
    ema = sorted[i].close * k + ema * (1 - k);
    res.push({ time: sorted[i].time as UTCTimestamp, value: Math.round(ema * 10) / 10 });
  }
  return res;
}

/** The tape: Candlestick chart on Lightweight Charts v5 with:
 * - Dynamic Support & Resistance Trendlines with projected rays and touch counts
 * - Exponential Moving Averages (EMA 20 in yellow, EMA 50 in red)
 * - Fixed Corner Legend / HUD ("kone me fix detail") showing live OHLCV & Intersected Zone
 * - Shaded Fair Value Gaps (FVG) and Order Blocks (OB) canvas overlay
 * - Volume histogram at bottom
 * - Smart Money Concepts (BOS / CHoCH) markers directly on bars
 * - POC, VAH, VAL, and Confluence S/R levels
 * - Multi-timezone axis support (UTC / Local / IST)
 */
export function PriceChart({
  bars,
  levels = [],
  markers = [],
  zones = [],
  trendlines = [],
  clusters = [],
  symbol = "BTC",
  interval = "15m",
  timezone = "IST",
  height = 400,
  showVolume = true,
  showMarkers = true,
  showLevels = true,
  showZones = true,
  showFvg = true,
  showOb = true,
  showTrendlines = true,
  showClusters = true,
  showEma = true,
}: {
  bars: Bar[];
  levels?: Level[];
  markers?: ChartMarker[];
  zones?: StructureZone[];
  trendlines?: TrendlineItem[];
  clusters?: HorizontalClusterItem[];
  symbol?: string;
  interval?: string;
  timezone?: "Local" | "UTC" | "IST";
  height?: number;
  showVolume?: boolean;
  showMarkers?: boolean;
  showLevels?: boolean;
  showZones?: boolean;
  showFvg?: boolean;
  showOb?: boolean;
  showTrendlines?: boolean;
  showClusters?: boolean;
  showEma?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const candleSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const ema20Series = useRef<ISeriesApi<"Line"> | null>(null);
  const ema50Series = useRef<ISeriesApi<"Line"> | null>(null);
  const markersPlugin = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);
  const live = useRef({ paintOverlay: () => {}, zones, showZones, showFvg, showOb });

  // Hover states for the fixed corner legend
  const [hoverBar, setHoverBar] = useState<Bar | null>(null);
  const [cursorPrice, setCursorPrice] = useState<number | null>(null);
  const [hoveredZone, setHoveredZone] = useState<StructureZone | null>(null);
  const [isLegendCollapsed, setIsLegendCollapsed] = useState(false);

  // Bumped every time the chart is rebuilt. Everything that fills the chart depends on it, so a
  // rebuild is always followed by a refill — without it, a rebuild triggered by something the
  // refill effects do not watch leaves the new chart empty of exactly those things.
  const [built, setBuilt] = useState(0);

  // Paint zones (FVG & OB) & Trendlines on the background canvas
  const paintOverlay = useCallback(() => {
    const canvas = canvasRef.current;
    const c = chart.current;
    const cs = candleSeries.current;
    const boxEl = box.current;
    if (!canvas || !c || !cs || !boxEl) return;

    const dpr = window.devicePixelRatio || 1;
    const w = boxEl.clientWidth;
    const h = height;

    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    if (!bars.length) return;

    const ts = c.timeScale();
    const rightScaleWidth = c.priceScale("right").width() || 65;
    const plotRight = Math.max(10, w - rightScaleWidth);

    // 1. Paint Trendlines (Diagonal Support & Resistance channels)
    if (showTrendlines && trendlines.length) {
      for (const t of trendlines) {
        const x1 = ts.timeToCoordinate(t.start_time as unknown as Time);
        const y1 = cs.priceToCoordinate(t.start_price);
        const x2 = ts.timeToCoordinate(t.end_time as unknown as Time);
        const y2 = cs.priceToCoordinate(t.end_price);
        const yCurrent = cs.priceToCoordinate(t.current_price);

        if (y1 === null && y2 === null && yCurrent === null) continue;

        const isSupport = t.kind === "support";
        const strokeColor = isSupport ? "rgba(37, 99, 235, 0.95)" : "rgba(220, 38, 38, 0.95)";
        const glowColor = isSupport ? "rgba(59, 130, 246, 0.2)" : "rgba(239, 68, 68, 0.2)";

        const safeX1 = x1 !== null ? Math.max(0, x1) : 0;
        const safeY1 = y1 !== null ? y1 : (y2 !== null ? y2 : 0);
        const safeX2 = x2 !== null ? Math.max(0, x2) : plotRight;
        const safeY2 = y2 !== null ? y2 : (yCurrent !== null ? yCurrent : safeY1);

        ctx.save();
        // Subtle glow
        ctx.strokeStyle = glowColor;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(safeX1, safeY1);
        ctx.lineTo(safeX2, safeY2);
        ctx.stroke();

        // Main trendline
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(safeX1, safeY1);
        ctx.lineTo(safeX2, safeY2);
        ctx.stroke();

        // Forward projected dashed ray
        if (x2 !== null && yCurrent !== null && x2 < plotRight) {
          ctx.setLineDash([4, 4]);
          ctx.strokeStyle = strokeColor;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(safeX2, safeY2);
          ctx.lineTo(plotRight, yCurrent);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        // Draw dots at swing anchor points
        if (x1 !== null && y1 !== null) {
          ctx.fillStyle = strokeColor;
          ctx.beginPath();
          ctx.arc(x1, y1, 3.5, 0, Math.PI * 2);
          ctx.fill();
        }
        if (x2 !== null && y2 !== null) {
          ctx.fillStyle = strokeColor;
          ctx.beginPath();
          ctx.arc(x2, y2, 3.5, 0, Math.PI * 2);
          ctx.fill();
        }

        // Label along the line
        const labelX = Math.min(plotRight - 120, Math.max(safeX1 + 10, (safeX1 + safeX2) / 2));
        const labelY = (safeY1 + safeY2) / 2 - 8;
        ctx.fillStyle = strokeColor;
        ctx.font = "bold 9px ui-monospace, SFMono-Regular, monospace";
        ctx.fillText(`${t.label}`, labelX, Math.max(14, labelY));

        ctx.restore();
      }
    }

    // 2. Paint Horizontal Multi-Swing Cross Levels (Double/Triple Tops/Bottoms)
    if (showClusters && clusters.length) {
      for (const c of clusters) {
        const y = cs.priceToCoordinate(c.price);
        if (y === null) continue;

        const isSupport = c.kind.includes("bottom") || c.kind.includes("support");
        const strokeColor = isSupport ? "rgba(16, 185, 129, 0.95)" : "rgba(239, 68, 68, 0.95)";
        const glowColor = isSupport ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.15)";

        ctx.save();
        // Subtle glow along level
        ctx.strokeStyle = glowColor;
        ctx.lineWidth = 3.5;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(plotRight, y);
        ctx.stroke();

        // Horizontal dashed line across chart
        ctx.setLineDash([5, 4]);
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(plotRight, y);
        ctx.stroke();
        ctx.setLineDash([]);

        // Paint touch markers at pivot swing times
        if (c.touch_times && c.touch_times.length > 0) {
          ctx.fillStyle = strokeColor;
          for (const tt of c.touch_times) {
            const tx = ts.timeToCoordinate(tt as unknown as Time);
            if (tx !== null && tx >= 0 && tx <= plotRight) {
              ctx.beginPath();
              ctx.arc(tx, y, 3.5, 0, Math.PI * 2);
              ctx.fill();
            }
          }
        }

        // Label on the right
        const formattedKind = c.kind
          .split("_")
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(" ");
        const text = `${formattedKind} (${c.touches}x) $${Math.round(c.price)}`;
        ctx.font = "bold 9px ui-monospace, SFMono-Regular, monospace";
        const textWidth = ctx.measureText(text).width;
        const textX = Math.max(8, plotRight - textWidth - 14);

        // Small badge background behind text
        ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
        ctx.fillRect(textX - 4, y - 13, textWidth + 8, 13);
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = 0.75;
        ctx.strokeRect(textX - 4, y - 13, textWidth + 8, 13);

        ctx.fillStyle = strokeColor;
        ctx.fillText(text, textX, y - 3);

        ctx.restore();
      }
    }

    // 3. Paint Zones (FVG & OB)
    if (showZones) {
      const activeZones = zones.filter((z) => {
        if (z.kind === "fvg" && !showFvg) return false;
        if (z.kind === "ob" && !showOb) return false;
        if (z.state === "invalidated") return false;
        return true;
      });

      for (const z of activeZones) {
        const yTop = cs.priceToCoordinate(z.high);
        const yBot = cs.priceToCoordinate(z.low);
        if (yTop === null || yBot === null) continue;

        const y = Math.min(yTop, yBot);
        const boxHeight = Math.max(2, Math.abs(yBot - yTop));

        let x = z.created_at ? ts.timeToCoordinate(z.created_at as unknown as Time) : 0;
        if (x === null) x = 0;
        x = Math.max(0, x);

        const boxWidth = Math.max(8, plotRight - x);

        let rgb = [124, 58, 237];
        let label = "FVG";

        if (z.kind === "fvg") {
          rgb = z.direction === "bull" ? [16, 185, 129] : [239, 68, 68];
          label = z.direction === "bull" ? "Bull FVG" : "Bear FVG";
        } else if (z.kind === "ob") {
          rgb = z.direction === "bull" ? [217, 119, 6] : [194, 65, 12];
          label = z.direction === "bull" ? "Bull OB" : "Bear OB";
        } else if (z.kind === "sr") {
          rgb = [13, 148, 136];
          label = "S/R";
        }

        const alphaMap: Record<string, number> = {
          fresh: 0.18,
          tested: 0.11,
          mitigated: 0.05,
          invalidated: 0.02,
        };
        const alpha = alphaMap[z.state] ?? 0.12;

        ctx.fillStyle = `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
        ctx.fillRect(x, y, boxWidth, boxHeight);

        ctx.strokeStyle = `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${Math.min(0.65, alpha * 2.6)})`;
        ctx.lineWidth = 1;
        ctx.strokeRect(x, y + 0.5, boxWidth, boxHeight);

        if (boxHeight >= 11 && boxWidth > 55) {
          ctx.fillStyle = `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.95)`;
          ctx.font = "bold 9px ui-monospace, SFMono-Regular, monospace";
          const stateSuffix = z.state === "fresh" ? " [Fresh]" : z.state === "tested" ? " [Tested]" : "";
          ctx.fillText(
            `${label}${stateSuffix} $${Math.round(z.low)}–$${Math.round(z.high)}`,
            x + 6,
            y + Math.min(boxHeight - 2, 10),
          );
        }
      }
    }
  }, [bars.length, clusters, height, showClusters, showFvg, showOb, showTrendlines, showZones, trendlines, zones]);

  // Initialize chart
  useEffect(() => {
    if (!box.current) return;

    const c = createChart(box.current, {
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
        scaleMargins: { top: 0.08, bottom: showVolume ? 0.22 : 0.08 },
      },
      timeScale: {
        borderColor: "#e2e8f0",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: unknown) => {
          const ts = typeof time === "number" ? time : Number(time);
          const tz = timezone === "IST" ? "Asia/Kolkata" : timezone === "UTC" ? "UTC" : undefined;
          const d = new Date(ts * 1000);
          return d.toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false });
        },
      },
      localization: {
        timeFormatter: (ts: number) => formatChartTime(ts, timezone),
      },
      crosshair: {
        mode: 1,
        vertLine: { color: "#94a3b8", width: 1, style: LineStyle.Dotted },
        horzLine: { color: "#94a3b8", width: 1, style: LineStyle.Dotted },
      },
      autoSize: true,
    });

    // Candlestick series
    const cs = c.addSeries(CandlestickSeries, {
      upColor: "#00b852",
      downColor: "#eb5757",
      borderVisible: false,
      wickUpColor: "#00b852",
      wickDownColor: "#eb5757",
      priceFormat: { type: "price", precision: 1, minMove: 0.1 },
    });
    candleSeries.current = cs;

    // Volume series on bottom scale
    if (showVolume) {
      const vs = c.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
      });
      c.priceScale("vol").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      volumeSeries.current = vs;
    }

    // EMA 20 (Yellow) and EMA 50 (Red) Lines
    const e20 = c.addSeries(LineSeries, {
      color: "#eab308",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    ema20Series.current = e20;

    const e50 = c.addSeries(LineSeries, {
      color: "#ef4444",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    ema50Series.current = e50;

    // Series markers plugin for SMC breaks
    markersPlugin.current = createSeriesMarkers(cs, []);

    // Listen to time scale visible range changes to redraw canvas zones & trendlines
    c.timeScale().subscribeVisibleLogicalRangeChange(() => {
      live.current.paintOverlay();
    });

    // Crosshair move listener for OHLCV hover, cursor price & Zone detection
    c.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData.get(cs)) {
        setHoverBar(null);
        setHoveredZone(null);
        setCursorPrice(null);
        return;
      }
      const data = param.seriesData.get(cs) as { open?: number; high?: number; low?: number; close?: number } | undefined;
      const volData = volumeSeries.current ? (param.seriesData.get(volumeSeries.current) as { value?: number } | undefined) : undefined;
      if (data && data.open !== undefined && data.close !== undefined) {
        setHoverBar({
          time: Number(param.time),
          open: data.open,
          high: data.high ?? data.open,
          low: data.low ?? data.open,
          close: data.close,
          volume: volData?.value ?? 0,
        });

        if (param.point) {
          const hoveredPrice = cs.coordinateToPrice(param.point.y);
          if (hoveredPrice !== null) {
            setCursorPrice(hoveredPrice);
            const now = live.current;
            if (now.showZones) {
              const match = now.zones.find(
                (z) =>
                  z.state !== "invalidated" &&
                  ((z.kind === "fvg" && now.showFvg) || (z.kind === "ob" && now.showOb)) &&
                  hoveredPrice >= z.low &&
                  hoveredPrice <= z.high,
              );
              setHoveredZone(match || null);
            }
          }
        }
      }
    });

    chart.current = c;
    setBuilt((n) => n + 1);  // the chart is empty: every effect that fills it now runs again

    const resizeObserver = new ResizeObserver(() => {
      if (!box.current) return;
      c.applyOptions({ width: box.current.clientWidth, height });
      live.current.paintOverlay();
    });
    resizeObserver.observe(box.current);

    return () => {
      resizeObserver.disconnect();
      c.remove();
      chart.current = null;
      candleSeries.current = null;
      volumeSeries.current = null;
      ema20Series.current = null;
      ema50Series.current = null;
      markersPlugin.current = null;
      priceLines.current = [];
    };
  }, [height, showVolume, timezone]);

  // Set candlestick, volume, and EMA data
  useEffect(() => {
    if (!candleSeries.current || !bars.length) return;

    const sorted = [...bars].sort((a, b) => a.time - b.time);
    const uniqueBars: Bar[] = [];
    const seenTimes = new Set<number>();
    for (const b of sorted) {
      if (!seenTimes.has(b.time)) {
        seenTimes.add(b.time);
        uniqueBars.push(b);
      }
    }

    candleSeries.current.setData(
      uniqueBars.map((b) => ({
        time: b.time as UTCTimestamp,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );

    if (volumeSeries.current && showVolume) {
      volumeSeries.current.setData(
        uniqueBars.map((b) => ({
          time: b.time as UTCTimestamp,
          value: b.volume || 0,
          color: b.close >= b.open ? "rgba(0, 184, 82, 0.35)" : "rgba(235, 87, 87, 0.35)",
        })),
      );
    }

    // Set EMA series
    if (ema20Series.current) {
      if (showEma) {
        ema20Series.current.setData(calcEma(uniqueBars, 20));
      } else {
        ema20Series.current.setData([]);
      }
    }
    if (ema50Series.current) {
      if (showEma) {
        ema50Series.current.setData(calcEma(uniqueBars, 50));
      } else {
        ema50Series.current.setData([]);
      }
    }

    setTimeout(paintOverlay, 50);
  }, [bars, built, paintOverlay, showEma, showVolume]);

  // Set levels
  useEffect(() => {
    const s = candleSeries.current;
    if (!s) return;

    priceLines.current.forEach((l) => s.removePriceLine(l));
    priceLines.current = [];

    if (!showLevels) return;

    priceLines.current = levels.map((l) =>
      s.createPriceLine({
        price: l.price,
        color: l.color,
        lineWidth: 1,
        lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid,
        axisLabelVisible: true,
        title: l.label,
      }),
    );
  }, [built, levels, showLevels]);

  // Set SMC Markers (CHoCH & BOS)
  useEffect(() => {
    if (!markersPlugin.current) return;
    if (!showMarkers || !markers.length) {
      markersPlugin.current.setMarkers([]);
      return;
    }

    const sortedMarkers = [...markers].sort((a, b) => a.time - b.time);
    const chartMarkers: SeriesMarker<Time>[] = sortedMarkers.map((m) => {
      const isChoch = m.kind === "choch";
      const isUp = m.direction === "up";

      return {
        time: m.time as UTCTimestamp,
        position: isUp ? "belowBar" : "aboveBar",
        shape: isUp ? "arrowUp" : "arrowDown",
        color: isChoch ? (isUp ? "#059669" : "#dc2626") : isUp ? "#10b981" : "#ef4444",
        text: isChoch ? (isUp ? "CHoCH ▲" : "CHoCH ▼") : isUp ? "BOS ▲" : "BOS ▼",
      };
    });

    markersPlugin.current.setMarkers(chartMarkers);
  }, [built, markers, showMarkers]);

  // Repaint overlay when zones, trendlines or visibility toggles change
  useEffect(() => {
    paintOverlay();
  }, [paintOverlay]);

  // What the chart's own listeners need, read when they fire rather than closed over at creation.
  // Keeping these out of the chart's dependencies is what stops it being torn down and rebuilt on
  // every data refresh — which emptied it of markers and levels, and reset the user's zoom with it.
  useEffect(() => {
    live.current = { paintOverlay, zones, showZones, showFvg, showOb };
  });

  const activeBar = hoverBar || (bars.length > 0 ? bars[bars.length - 1] : null);
  const changePct = activeBar ? ((activeBar.close - activeBar.open) / activeBar.open) * 100 : 0;
  const changeUsd = activeBar ? activeBar.close - activeBar.open : 0;

  // Find nearest price line level if cursor is near
  const nearestLevel = cursorPrice !== null && levels.length > 0
    ? levels.reduce((prev, curr) =>
        Math.abs(curr.price - cursorPrice) < Math.abs(prev.price - cursorPrice) ? curr : prev
      )
    : null;
  const isNearLevel = nearestLevel && cursorPrice !== null && Math.abs(nearestLevel.price - cursorPrice) / cursorPrice < 0.004;

  return (
    <div ref={containerRef} className="relative w-full">
      {/* Chart & Overlay Canvas Container with Fixed Corner Legend */}
      <div
        className="relative w-full rounded-md border bg-white overflow-hidden shadow-xs"
        style={{ height }}
        onMouseLeave={() => {
          setHoverBar(null);
          setHoveredZone(null);
          setCursorPrice(null);
        }}
      >
        <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 z-0" />
        <div ref={box} className="relative z-10 h-full w-full" />

        {/* ====================================================================== */}
        {/* FIXED CORNER LEGEND / HUD ("kone me fix detail")                        */}
        {/* ====================================================================== */}
        {activeBar && (
          <div
            className={`absolute top-2.5 left-2.5 z-20 rounded-lg border border-slate-200/90 bg-white/92 backdrop-blur-md shadow-md transition-all duration-150 select-none text-xs font-mono ${
              isLegendCollapsed ? "w-auto px-2.5 py-1.5" : "w-[310px] sm:w-[350px] p-2.5"
            }`}
          >
            {/* Header: Symbol, Time, Hover Status, and Minimize Toggle */}
            <div className="flex items-center justify-between gap-1.5">
              <div className="flex items-center gap-1.5 min-w-0">
                <span className="font-bold text-slate-900 tracking-wide text-[12px]">
                  {symbol}USD
                </span>
                <span className="rounded bg-slate-100 px-1 py-0.2 text-[9.5px] font-semibold text-slate-600">
                  {interval}
                </span>
                <span className="text-slate-300">·</span>
                <span className="text-[10px] text-slate-600 truncate font-sans">
                  {formatChartTime(activeBar.time, timezone)}
                </span>
              </div>

              <div className="flex items-center gap-1 shrink-0">
                <span
                  className={`rounded-full px-1.5 py-0.2 text-[9px] font-semibold uppercase ${
                    hoverBar
                      ? "bg-blue-100 text-blue-700 border border-blue-200"
                      : "bg-emerald-50 text-emerald-700 border border-emerald-200"
                  }`}
                >
                  {hoverBar ? "Hover" : "Latest"}
                </span>
                <button
                  onClick={() => setIsLegendCollapsed(!isLegendCollapsed)}
                  className="size-5 rounded flex items-center justify-center text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
                  title={isLegendCollapsed ? "Expand legend detail" : "Collapse legend detail"}
                >
                  {isLegendCollapsed ? <ChevronDown className="size-3" /> : <ChevronUp className="size-3" />}
                </button>
              </div>
            </div>

            {/* Collapsed Compact View */}
            {isLegendCollapsed ? (
              <div className="mt-1 flex items-center gap-2 text-[11px]">
                <span className="font-bold text-slate-900">${activeBar.close.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</span>
                <span className={`font-semibold ${changePct >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
                  {changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
                </span>
                {hoveredZone && (
                  <span className="text-[9.5px] font-semibold text-purple-700 bg-purple-50 px-1 rounded border border-purple-200">
                    Inside FVG
                  </span>
                )}
              </div>
            ) : (
              <>
                {/* Expanded Full OHLCV Details Grid */}
                <div className="mt-2 grid grid-cols-4 gap-1.5 border-t border-slate-100 pt-2 text-[11px] leading-tight">
                  <div className="rounded bg-slate-50/80 px-1.5 py-1 border border-slate-100">
                    <span className="text-slate-400 text-[9px] uppercase tracking-wider block">Open</span>
                    <span className="font-semibold text-slate-900">${activeBar.open.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</span>
                  </div>
                  <div className="rounded bg-slate-50/80 px-1.5 py-1 border border-slate-100">
                    <span className="text-slate-400 text-[9px] uppercase tracking-wider block">High</span>
                    <span className="font-semibold text-slate-900">${activeBar.high.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</span>
                  </div>
                  <div className="rounded bg-slate-50/80 px-1.5 py-1 border border-slate-100">
                    <span className="text-slate-400 text-[9px] uppercase tracking-wider block">Low</span>
                    <span className="font-semibold text-slate-900">${activeBar.low.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</span>
                  </div>
                  <div className="rounded bg-slate-50/80 px-1.5 py-1 border border-slate-100">
                    <span className="text-slate-400 text-[9px] uppercase tracking-wider block">Close</span>
                    <span className={`font-semibold ${changePct >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
                      ${activeBar.close.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
                    </span>
                  </div>
                </div>

                {/* Change, Volume & EMA Status */}
                <div className="mt-2 flex items-center justify-between border-t border-slate-100 pt-1.5 text-[10.5px]">
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-400">Change:</span>
                    <span className={`font-bold ${changePct >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
                      {changePct >= 0 ? "+" : ""}{changeUsd.toFixed(1)} ({changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%)
                    </span>
                  </div>

                  {activeBar.volume ? (
                    <div className="text-slate-600">
                      Vol: <b className="text-slate-900">{Math.round(activeBar.volume).toLocaleString()}</b>
                    </div>
                  ) : null}
                </div>

                {/* EMA Indicators Status */}
                {showEma && (
                  <div className="mt-1 flex items-center gap-2 text-[10px]">
                    <span className="flex items-center gap-1 font-semibold text-amber-600">
                      <span className="size-2 rounded-full bg-amber-500" /> EMA 20
                    </span>
                    <span className="flex items-center gap-1 font-semibold text-rose-600">
                      <span className="size-2 rounded-full bg-rose-500" /> EMA 50
                    </span>
                  </div>
                )}

                {/* Cursor Price & Nearby Level */}
                {cursorPrice !== null && (
                  <div className="mt-1.5 flex items-center justify-between rounded bg-slate-50 px-2 py-1 text-[10px] text-slate-600 border border-slate-100">
                    <div className="flex items-center gap-1">
                      <span className="text-slate-400">Pointer:</span>
                      <b className="font-mono text-slate-900">${cursorPrice.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</b>
                    </div>
                    {isNearLevel && nearestLevel && (
                      <span className="font-semibold text-blue-600">
                        At {nearestLevel.label} (${nearestLevel.price.toLocaleString()})
                      </span>
                    )}
                  </div>
                )}

                {/* Intersected Zone / FVG Highlight Banner */}
                {hoveredZone && (
                  <div
                    className={`mt-2 rounded-md border p-2 text-[10.5px] ${
                      hoveredZone.kind === "fvg"
                        ? hoveredZone.direction === "bull"
                          ? "border-emerald-300 bg-emerald-50/90 text-emerald-950"
                          : "border-rose-300 bg-rose-50/90 text-rose-950"
                        : "border-amber-300 bg-amber-50/90 text-amber-950"
                    }`}
                  >
                    <div className="flex items-center justify-between font-bold">
                      <span className="flex items-center gap-1">
                        <span className="size-2 rounded-full bg-current" />
                        {hoveredZone.kind === "fvg" ? "Fair Value Gap" : "Order Block"} ({hoveredZone.direction === "bull" ? "Bullish Demand" : "Bearish Supply"})
                      </span>
                      <span className="capitalize text-[9.5px] bg-white/80 px-1.5 py-0.2 rounded border border-current font-semibold">
                        {hoveredZone.state}
                      </span>
                    </div>

                    <div className="mt-1 flex items-center justify-between text-[10px] font-mono opacity-90">
                      <span>
                        ${num(hoveredZone.low, 1)} – ${num(hoveredZone.high, 1)}
                      </span>
                      <span>
                        {hoveredZone.width ? `$${Math.round(hoveredZone.width)} wide` : ""}
                        {hoveredZone.fill_pct !== undefined && hoveredZone.fill_pct > 0 ? ` · ${Math.round(hoveredZone.fill_pct * 100)}% filled` : " · 0% filled"}
                      </span>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function num(val: number, decimals = 1): string {
  return Number(val).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
