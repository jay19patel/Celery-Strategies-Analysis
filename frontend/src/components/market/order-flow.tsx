"use client";

// The auction read: the day's bias, the option book's gamma, the tape's order flow and its
// footprint. Every number here comes from the backend's own readings; nothing is recomputed.

import { Activity, Compass, Footprints, Sigma } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Empty, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { DayBias, GammaExposure, OptionsPremium, OrderFlow } from "@/lib/types";
import { cn } from "@/lib/utils";

const TOOLTIP = { backgroundColor: "var(--popover)", color: "var(--popover-foreground)", borderRadius: 12, border: "none", boxShadow: "0 8px 30px rgb(0 0 0 / 0.16)", fontSize: 12 };
const TICK = { fontSize: 10, fill: "var(--chart-text)" };

/** $12.3M, $480K, $950 */
function money(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  const s = a >= 1e9 ? `${(a / 1e9).toFixed(2)}B` : a >= 1e6 ? `${(a / 1e6).toFixed(2)}M` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}K` : a.toFixed(0);
  return `${v < 0 ? "-" : ""}$${s}`;
}

function tone(side: string | null | undefined): "profit" | "loss" | "default" {
  return side === "bullish" || side === "buy" || side === "positive" ? "profit" : side === "bearish" || side === "sell" || side === "negative" ? "loss" : "default";
}

function Stat({ label, value, className }: { label: string; value: React.ReactNode; className?: string }) {
  return (
    <div className="rounded-lg bg-secondary/60 px-2.5 py-2">
      <div className="text-[10.5px] text-muted-foreground">{label}</div>
      <div className={cn("num mt-0.5 text-[13px] font-semibold", className)}>{value}</div>
    </div>
  );
}

function Premium({ p }: { p: OptionsPremium | null | undefined }) {
  if (!p) return <p className="text-[11.5px] text-muted-foreground">No option trades recorded today yet.</p>;
  const calls = p.call_bought - p.call_sold;
  const puts = p.put_bought - p.put_sold;
  return (
    <div className="grid grid-cols-2 gap-2 text-[12px]">
      <Stat label="Calls bought / sold" value={<>{money(p.call_bought)} / {money(p.call_sold)}</>} className={calls >= 0 ? "text-profit" : "text-loss"} />
      <Stat label="Puts bought / sold" value={<>{money(p.put_bought)} / {money(p.put_sold)}</>} className={puts >= 0 ? "text-loss" : "text-profit"} />
    </div>
  );
}

// -- the day's bias ------------------------------------------------------------------------------------

export function BiasCard({ underlying }: { underlying: string }) {
  const { data } = useApi<DayBias>(`/desk/bias?underlying=${underlying}`, { refreshInterval: 60_000 });
  const score = data?.score ?? 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Compass className="size-4 text-primary" /> Daily bias
        </CardTitle>
        {data?.ready && (
          <div className="flex items-center gap-1.5">
            <Badge variant={tone(data.label)}>{data.label}</Badge>
            <Badge variant="outline">{data.style} day</Badge>
            {data.gamma && <Badge variant={tone(data.gamma)}>{data.gamma} gamma</Badge>}
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-3 text-[12.5px]">
        {!data && <Loading rows={3} />}
        {data && !data.ready && <Empty title="No bias yet">{data.note}</Empty>}
        {data?.ready && (
          <>
            <div>
              <div className="relative h-2 rounded-full bg-secondary">
                <div className="absolute top-0 h-2 w-px bg-muted-foreground/50" style={{ left: "50%" }} />
                <div
                  className={cn("absolute top-0 h-2 rounded-full", score >= 0 ? "bg-profit" : "bg-loss")}
                  style={score >= 0 ? { left: "50%", width: `${score * 50}%` } : { right: "50%", width: `${-score * 50}%` }}
                />
              </div>
              <div className="mt-1 flex justify-between text-[10.5px] text-muted-foreground">
                <span>bearish</span>
                <span className="num font-semibold text-foreground">{score >= 0 ? "+" : ""}{score.toFixed(2)}</span>
                <span>bullish</span>
              </div>
            </div>
            <ul className="space-y-1">
              {data.reasons?.map((r) => (
                <li key={r.text} className="flex gap-2 leading-snug">
                  <span className={cn("num w-10 shrink-0 text-right font-semibold", r.points > 0 ? "text-profit" : r.points < 0 ? "text-loss" : "text-muted-foreground")}>
                    {r.points > 0 ? "+" : ""}{r.points.toFixed(2)}
                  </span>
                  <span className="text-muted-foreground">{r.text}</span>
                </li>
              ))}
            </ul>
            {data.levels && (
              <div className="grid grid-cols-3 gap-2">
                <Stat label="Yesterday VAH" value={num(data.levels.vah, 2)} />
                <Stat label="POC" value={num(data.levels.poc, 2)} />
                <Stat label="VAL" value={num(data.levels.val, 2)} />
              </div>
            )}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-2 text-[11.5px] text-muted-foreground">
              <span>
                Track record:{" "}
                <span className="num font-semibold text-foreground">
                  {data.record.hit_rate_pct === null ? "—" : `${data.record.hit_rate_pct}%`}
                </span>{" "}
                right over {data.record.scored} scored day{data.record.scored === 1 ? "" : "s"}
              </span>
              <span className="flex gap-0.5">
                {data.record.days.slice(0, 10).reverse().map((d) => (
                  <span
                    key={d.day}
                    title={`${new Date(d.day * 1000).toISOString().slice(0, 10)}: ${d.label}${d.ret_pct !== null ? `, day ${d.ret_pct > 0 ? "+" : ""}${d.ret_pct.toFixed(2)}%` : ", open"}`}
                    className={cn("size-2.5 rounded-sm", d.correct === true ? "bg-profit" : d.correct === false ? "bg-loss" : "bg-secondary")}
                  />
                ))}
              </span>
            </div>
            <p className="text-[11px] text-muted-foreground">{data.record.note}</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// -- gamma exposure ------------------------------------------------------------------------------------

export function GexCard({ underlying }: { underlying: string }) {
  const { data } = useApi<GammaExposure>(`/desk/gex?underlying=${underlying}`, { refreshInterval: 60_000 });
  const strikes = (data?.by_strike ?? []).map((r) => ({ ...r, net: r.call + r.put }));
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Sigma className="size-4 text-primary" /> Gamma exposure (GEX)
        </CardTitle>
        {data?.ready && <Badge variant={tone(data.regime)}>{data.regime} gamma</Badge>}
      </CardHeader>
      <CardContent className="space-y-3 text-[12.5px]">
        {!data && <Loading rows={3} />}
        {data && !data.ready && <Empty title="No gamma to weigh">{data.note}</Empty>}
        {data?.ready && (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="Net GEX / 1% move" value={money(data.net)} className={(data.net ?? 0) >= 0 ? "text-profit" : "text-loss"} />
              <Stat label="Gamma flip" value={data.flip ? num(data.flip, 0) : "none near"} />
              <Stat label="Call wall" value={data.call_wall ? num(data.call_wall, 0) : "—"} className="text-profit" />
              <Stat label="Put wall" value={data.put_wall ? num(data.put_wall, 0) : "—"} className="text-loss" />
            </div>
            <p className="text-[11.5px] text-muted-foreground">
              {data.regime === "positive"
                ? "Positive gamma: hedgers sell rallies and buy dips, so moves are damped and ranges hold."
                : "Negative gamma: hedgers chase the move, so breaks tend to run."}{" "}
              Assumes dealers are long calls and short puts; on a book this size, read it as a regime, not a force.
            </p>
            {strikes.length > 0 && (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={strikes} margin={{ top: 6, right: 6, left: -10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="strike" tick={TICK} tickLine={false} tickFormatter={(v: number) => num(v, 0)} />
                    <YAxis tick={TICK} tickLine={false} tickFormatter={(v: number) => money(v)} width={60} />
                    <Tooltip contentStyle={TOOLTIP} formatter={(v) => money(Number(v))} labelFormatter={(v) => `strike ${num(Number(v), 0)}`} />
                    {data.spot && <ReferenceLine x={strikes.reduce((b, r) => (Math.abs(r.strike - data.spot!) < Math.abs(b.strike - data.spot!) ? r : b), strikes[0]).strike} stroke="var(--chart-ref)" strokeDasharray="3 3" label={{ value: "spot", fill: "var(--chart-text)", fontSize: 10, position: "top" }} />}
                    <Bar dataKey="net" name="net GEX" isAnimationActive={false}>
                      {strikes.map((r) => (
                        <Cell key={r.strike} fill={r.net >= 0 ? "var(--profit)" : "var(--loss)"} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
            {data.history.length > 1 && (
              <div className="h-24">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={data.history} margin={{ top: 4, right: 6, left: -10, bottom: 0 }}>
                    <XAxis dataKey="ts" tick={TICK} tickLine={false} tickFormatter={(v: number) => time(v).slice(0, 5)} minTickGap={40} />
                    <YAxis tick={TICK} tickLine={false} tickFormatter={(v: number) => money(v)} width={60} />
                    <ReferenceLine y={0} stroke="var(--chart-ref)" />
                    <Tooltip contentStyle={TOOLTIP} formatter={(v) => money(Number(v))} labelFormatter={(v) => time(Number(v))} />
                    <Line type="monotone" dataKey="net" name="net GEX" stroke="#6366f1" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
            <div>
              <div className="mb-1.5 text-[11.5px] font-medium text-muted-foreground">Option premium that crossed the spread today</div>
              <Premium p={data.options_today} />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// -- order flow ----------------------------------------------------------------------------------------

export function FlowCard({ underlying }: { underlying: string }) {
  const { data } = useApi<OrderFlow>(`/desk/flow?underlying=${underlying}&minutes=5`, { refreshInterval: 30_000 });
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Activity className="size-4 text-primary" /> Order flow (5m, live tape)
        </CardTitle>
        {data?.divergence && <Badge variant={tone(data.divergence.side)}>{data.divergence.side} divergence</Badge>}
      </CardHeader>
      <CardContent className="space-y-3 text-[12.5px]">
        {!data && <Loading rows={3} />}
        {data && !data.ready && <Empty title="Recording the tape">{data.note}</Empty>}
        {data?.ready && (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="Last bar delta" value={num(data.delta, 0)} className={(data.delta ?? 0) >= 0 ? "text-profit" : "text-loss"} />
              <Stat label="CVD (24h)" value={num(data.cvd, 0)} className={(data.cvd ?? 0) >= 0 ? "text-profit" : "text-loss"} />
              <Stat
                label="Book near mid"
                value={data.book_imbalance == null ? "—" : `${data.book_imbalance > 0 ? "bid" : "offer"} ${pct(Math.abs(data.book_imbalance), 0, true)}`}
                className={(data.book_imbalance ?? 0) >= 0 ? "text-profit" : "text-loss"}
              />
              <Stat label="Volume POC" value={num(data.profile?.poc, 2)} />
            </div>
            {data.series && data.series.length > 1 && (
              <div className="h-36">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={data.series} margin={{ top: 4, right: 6, left: -10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="time" tick={TICK} tickLine={false} tickFormatter={(v: number) => time(v).slice(0, 5)} minTickGap={40} />
                    <YAxis yAxisId="cvd" tick={TICK} tickLine={false} tickFormatter={(v: number) => num(v, 0)} width={60} />
                    <YAxis yAxisId="price" orientation="right" hide domain={["auto", "auto"]} />
                    <Tooltip contentStyle={TOOLTIP} labelFormatter={(v) => time(Number(v))} formatter={(v) => num(Number(v), 2)} />
                    <Line yAxisId="cvd" type="monotone" dataKey="cvd" name="CVD" stroke="#0ea5e9" strokeWidth={1.75} dot={false} isAnimationActive={false} />
                    <Line yAxisId="price" type="monotone" dataKey="close" name="price" stroke="var(--chart-ref)" strokeWidth={1} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
            {data.divergence && <p className="text-[11.5px] text-muted-foreground">{data.divergence.detail}.</p>}
            {(data.stacked ?? []).length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {data.stacked!.map((s) => (
                  <Badge key={`${s.side}${s.low}`} variant={tone(s.side)}>
                    stacked {s.side === "buy" ? "buying" : "selling"} {num(s.low, 2)}–{num(s.high, 2)} ({s.levels})
                  </Badge>
                ))}
              </div>
            )}
            <div>
              <div className="mb-1.5 text-[11.5px] font-medium text-muted-foreground">Absorption and exhaustion (last 24h)</div>
              {(data.events ?? []).length === 0 ? (
                <p className="text-[11.5px] text-muted-foreground">None: no climax that price refused or could not hold.</p>
              ) : (
                <ul className="space-y-1">
                  {data.events!.slice().reverse().slice(0, 6).map((e) => (
                    <li key={`${e.kind}${e.time}`} className="flex items-start gap-2 leading-snug">
                      <Badge variant={tone(e.side)} className="shrink-0">{e.kind}</Badge>
                      <span className="text-muted-foreground">
                        <span className="num">{time(e.time).slice(0, 5)}</span> · {e.detail}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// -- footprint and delta profile -------------------------------------------------------------------------

export function FootprintCard({ underlying }: { underlying: string }) {
  const { data } = useApi<OrderFlow>(`/desk/flow?underlying=${underlying}&minutes=5`, { refreshInterval: 30_000 });
  const bars = (data?.footprint ?? []).slice(-6);
  const prices = [...new Set(bars.flatMap((b) => b.levels.map((l) => l[0])))].sort((a, b) => b - a).slice(0, 24);
  const peak = Math.max(1, ...bars.flatMap((b) => b.levels.map((l) => l[1] + l[2])));
  const imbalanced = new Set((data?.imbalances ?? []).map((i) => `${i.side}${i.price}`));
  const last = bars.at(-1)?.time;
  const profile = (data?.profile?.rows ?? []).filter((r) => prices.includes(r.price));
  const deltaPeak = Math.max(1, ...profile.map((r) => Math.abs(r.delta)));
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Footprints className="size-4 text-primary" /> Footprint and delta profile
        </CardTitle>
        <span className="text-[11.5px] text-muted-foreground">sold × bought at each price, last 6 five-minute bars</span>
      </CardHeader>
      <CardContent className="text-[11.5px]">
        {!data && <Loading rows={4} />}
        {data && !data.ready && <Empty title="Recording the tape">{data.note}</Empty>}
        {data?.ready && bars.length > 0 && (
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[640px] border-separate border-spacing-0">
              <thead>
                <tr className="text-[10.5px] text-muted-foreground">
                  <th className="px-2 py-1 text-right font-medium">price</th>
                  {bars.map((b) => (
                    <th key={b.time} className={cn("px-2 py-1 text-center font-medium", b.delta >= 0 ? "text-profit" : "text-loss")}>
                      {time(b.time).slice(0, 5)} <span className="num">({b.delta >= 0 ? "+" : ""}{num(b.delta, 0)})</span>
                    </th>
                  ))}
                  <th className="px-2 py-1 text-left font-medium">delta profile (24h)</th>
                </tr>
              </thead>
              <tbody className="num">
                {prices.map((p) => {
                  const row = profile.find((r) => r.price === p);
                  const poc = p === data.profile?.poc;
                  return (
                    <tr key={p} className={cn(poc && "font-semibold")}>
                      <td className={cn("px-2 py-0.5 text-right text-muted-foreground", poc && "text-foreground")}>{num(p, 2)}{poc && " ◂POC"}</td>
                      {bars.map((b) => {
                        const level = b.levels.find((l) => l[0] === p);
                        if (!level) return <td key={b.time} className="px-2 py-0.5" />;
                        const [, bought, sold] = level;
                        const lean = bought - sold;
                        const strength = (bought + sold) / peak;
                        const buyImb = b.time === last && imbalanced.has(`buy${p}`);
                        const sellImb = b.time === last && imbalanced.has(`sell${p}`);
                        return (
                          <td
                            key={b.time}
                            className="px-2 py-0.5 text-center"
                            style={{ background: `color-mix(in oklab, var(${lean >= 0 ? "--profit" : "--loss"}) ${Math.round(6 + strength * 34)}%, transparent)` }}
                          >
                            <span className={cn(sellImb && "font-bold text-loss")}>{num(sold, 0)}</span>
                            <span className="text-muted-foreground"> × </span>
                            <span className={cn(buyImb && "font-bold text-profit")}>{num(bought, 0)}</span>
                          </td>
                        );
                      })}
                      <td className="px-2 py-0.5">
                        {row && (
                          <div className="flex items-center gap-1.5">
                            <div className={cn("h-2 rounded-sm", row.delta >= 0 ? "bg-profit" : "bg-loss")} style={{ width: `${Math.max(2, (Math.abs(row.delta) / deltaPeak) * 80)}px` }} />
                            <span className={row.delta >= 0 ? "text-profit" : "text-loss"}>{num(row.delta, 0)}</span>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="mt-2 px-5 text-[11px] text-muted-foreground">
              Bold numbers in the newest bar are diagonal imbalances (3× or more against the price one step away). Contracts, not dollars.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
