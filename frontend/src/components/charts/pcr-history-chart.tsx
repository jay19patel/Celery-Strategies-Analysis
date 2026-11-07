"use client";

import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Activity, BarChart2, Calendar, Scale, TrendingUp } from "lucide-react";

import { Empty, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { num, pct } from "@/lib/format";
import { useApi } from "@/lib/live";

export interface OptionsHistoryRow {
  ts: number;
  underlying: string;
  spot?: number | null;
  pcr_oi?: number | null;
  pcr_volume?: number | null;
  atm_iv?: number | null;
  call_oi?: number | null;
  put_oi?: number | null;
  max_pain?: number | null;
  skew_25d?: number | null;
}

export function PcrHistoryChart({
  underlying = "BTC",
  timezone = "Local",
}: {
  underlying: string;
  timezone?: "Local" | "UTC" | "IST";
}) {
  const [metric, setMetric] = useState<"pcr" | "iv" | "oi">("pcr");
  const { data: history, isLoading } = useApi<OptionsHistoryRow[]>(
    `/desk/options-history?underlying=${underlying}&hours=24`,
    { refreshInterval: 60_000 },
  );

  const chartData = useMemo(() => {
    if (!history?.length) return [];
    const fmt = (ts: number) => {
      const d = new Date(ts * 1000);
      if (timezone === "UTC") {
        return d.toLocaleTimeString("en-GB", { timeZone: "UTC", hour: "2-digit", minute: "2-digit" });
      }
      if (timezone === "IST") {
        return d.toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" });
      }
      return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    };
    return history.map((r) => ({
      ...r,
      timeStr: fmt(r.ts),
      pcrOi: r.pcr_oi != null ? Number(r.pcr_oi.toFixed(3)) : null,
      pcrVol: r.pcr_volume != null ? Number(r.pcr_volume.toFixed(3)) : null,
      atmIvPct: r.atm_iv != null ? Number((r.atm_iv * 100).toFixed(1)) : null,
      callOi: r.call_oi != null ? Math.round(r.call_oi) : 0,
      putOi: r.put_oi != null ? Math.round(r.put_oi) : 0,
    }));
  }, [history, timezone]);

  const latest = history?.[history.length - 1];

  if (isLoading && !history) {
    return <Loading rows={4} className="p-4" />;
  }

  if (!chartData.length) {
    return (
      <Card>
        <CardContent className="py-8">
          <Empty title="No 24h options history recorded yet. The minute recorder collects points automatically." />
        </CardContent>
      </Card>
    );
  }

  const pcrValue = latest?.pcr_oi ?? 1.0;
  const pcrStance = pcrValue >= 1.2 ? "Put Heavy (Bearish/Hedging)" : pcrValue <= 0.7 ? "Call Heavy (Bullish)" : "Balanced";

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Scale className="size-4 text-primary" />
              Put/Call Ratio (PCR) & Options Book History (24h)
            </CardTitle>
            <Badge
              variant={pcrValue >= 1.2 ? "loss" : pcrValue <= 0.7 ? "profit" : "default"}
              className="text-[10px]"
            >
              {pcrStance}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Per-minute recorded open interest, volume ratios, and ATM volatility from the options chain.
          </p>
        </div>

        {/* Metric Selector Toggle */}
        <div className="flex items-center rounded-lg bg-secondary/60 p-1">
          <button
            type="button"
            onClick={() => setMetric("pcr")}
            className={`rounded-md px-2.5 py-0.5 text-xs font-semibold transition-colors ${
              metric === "pcr"
                ? "bg-background text-foreground ring-1 ring-border"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            PCR Ratio
          </button>
          <button
            type="button"
            onClick={() => setMetric("iv")}
            className={`rounded-md px-2.5 py-0.5 text-xs font-semibold transition-colors ${
              metric === "iv"
                ? "bg-background text-foreground ring-1 ring-border"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            ATM IV (%)
          </button>
          <button
            type="button"
            onClick={() => setMetric("oi")}
            className={`rounded-md px-2.5 py-0.5 text-xs font-semibold transition-colors ${
              metric === "oi"
                ? "bg-background text-foreground ring-1 ring-border"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Call vs Put OI
          </button>
        </div>
      </CardHeader>

      <CardContent className="space-y-3 pt-2">
        {/* KPI Strip */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 font-mono text-xs">
          <div className="rounded bg-secondary/60 p-2">
            <div className="text-[10px] text-muted-foreground uppercase">PCR (Open Interest)</div>
            <div className="font-bold text-foreground text-sm">{latest?.pcr_oi ? latest.pcr_oi.toFixed(3) : "—"}</div>
          </div>
          <div className="rounded bg-secondary/60 p-2">
            <div className="text-[10px] text-muted-foreground uppercase">PCR (Volume)</div>
            <div className="font-bold text-foreground text-sm">{latest?.pcr_volume ? latest.pcr_volume.toFixed(3) : "—"}</div>
          </div>
          <div className="rounded bg-secondary/60 p-2">
            <div className="text-[10px] text-muted-foreground uppercase">Max Pain Strike</div>
            <div className="font-bold text-foreground text-sm">${latest?.max_pain ? num(latest.max_pain, 0) : "—"}</div>
          </div>
          <div className="rounded bg-secondary/60 p-2">
            <div className="text-[10px] text-muted-foreground uppercase">25Δ Skew</div>
            <div className="font-bold text-foreground text-sm">{latest?.skew_25d ? `${(latest.skew_25d * 100).toFixed(1)}%` : "—"}</div>
          </div>
        </div>

        {/* Chart View */}
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            {metric === "pcr" ? (
              <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="pcrGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#0ea5e9" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#0ea5e9" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="timeStr" tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} />
                <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} />
                <ReferenceLine y={1.0} stroke="var(--chart-ref)" strokeDasharray="3 3" label={{ value: "1.0 Neutral", fill: "var(--chart-ref)", fontSize: 10, position: "insideTopRight" }} />
                <Tooltip
                  contentStyle={{ backgroundColor: "var(--popover)", color: "var(--popover-foreground)", borderRadius: 12, border: "none", boxShadow: "0 8px 30px rgb(0 0 0 / 0.16)", fontSize: 12 }}
                  formatter={(val: unknown) => [String(val), "Put/Call OI"]}
                />
                <Area type="monotone" dataKey="pcrOi" stroke="#0ea5e9" strokeWidth={2} fillOpacity={1} fill="url(#pcrGradient)" animationDuration={450} animationEasing="ease-out" />
                <Line type="monotone" dataKey="pcrVol" stroke="#f59e0b" strokeWidth={1.5} dot={false} animationDuration={450} animationEasing="ease-out" />
              </AreaChart>
            ) : metric === "iv" ? (
              <LineChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="timeStr" tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} />
                <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} unit="%" />
                <Tooltip
                  contentStyle={{ backgroundColor: "var(--popover)", color: "var(--popover-foreground)", borderRadius: 12, border: "none", boxShadow: "0 8px 30px rgb(0 0 0 / 0.16)", fontSize: 12 }}
                  formatter={(val: unknown) => [`${val}%`, "ATM IV"]}
                />
                <Line type="monotone" dataKey="atmIvPct" stroke="#8b5cf6" strokeWidth={2} dot={false} animationDuration={450} animationEasing="ease-out" />
              </LineChart>
            ) : (
              <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="timeStr" tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} />
                <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "var(--chart-text)" }} tickLine={false} />
                <Tooltip
                  contentStyle={{ backgroundColor: "var(--popover)", color: "var(--popover-foreground)", borderRadius: 12, border: "none", boxShadow: "0 8px 30px rgb(0 0 0 / 0.16)", fontSize: 12 }}
                />
                <Area type="monotone" dataKey="callOi" stackId="1" stroke="#10b981" fill="#10b981" fillOpacity={0.6} name="Call OI" animationDuration={450} animationEasing="ease-out" />
                <Area type="monotone" dataKey="putOi" stackId="1" stroke="#ef4444" fill="#ef4444" fillOpacity={0.6} name="Put OI" animationDuration={450} animationEasing="ease-out" />
              </AreaChart>
            )}
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
