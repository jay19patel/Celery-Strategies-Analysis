"use client";

import { useEffect, useRef } from "react";
import { CandlestickSeries, ColorType, createChart, LineStyle, type IChartApi, type IPriceLine, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";

import type { Bar } from "@/lib/types";

export interface Level {
  price: number;
  label: string;
  color: string;
  dashed?: boolean;
}

/** The underlying's 15m candles with the selected structure's levels drawn across them. Mounted
 * once and updated in place, so a refresh never throws away the zoom someone set. */
export function PriceChart({ bars, levels, height = 260 }: { bars: Bar[]; levels: Level[]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#64748b", fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: "#f1f5f9" }, horzLines: { color: "#f1f5f9" } },
      rightPriceScale: { borderColor: "#e2e8f0" },
      timeScale: { borderColor: "#e2e8f0", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
      autoSize: true,
    });
    series.current = c.addSeries(CandlestickSeries, {
      upColor: "#0e8a3e", downColor: "#cf1f2e", borderVisible: false, wickUpColor: "#0e8a3e", wickDownColor: "#cf1f2e",
    });
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      lines.current = [];
    };
  }, [height]);

  useEffect(() => {
    series.current?.setData(bars.map((b) => ({ time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close })));
  }, [bars]);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    lines.current.forEach((l) => s.removePriceLine(l));
    lines.current = levels.map((l) =>
      s.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid, axisLabelVisible: true, title: l.label }),
    );
  }, [levels]);

  return <div ref={box} className="w-full" style={{ height }} />;
}
