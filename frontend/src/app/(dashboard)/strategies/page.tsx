"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Bot,
  Crosshair,
  ListFilter,
  Loader2,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { PageHeader } from "@/components/shared/page";
import { LoadError } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { ago, num } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { DirectionalStatus, SettingsResponse, SignalRow } from "@/lib/types";

export default function StrategiesPage() {
  const { mutate } = useSWRConfig();
  const [selectedStrategy, setSelectedStrategy] = useState<string>("");
  const [saving, setSaving] = useState<string>("");

  const { data: directional, error: dirErr } = useApi<DirectionalStatus>("/strategies/directional", {
    refreshInterval: 10_000,
  });
  const { data: signals } = useApi<SignalRow[]>(
    `/strategies/signals${selectedStrategy ? `?strategy=${selectedStrategy}` : ""}`,
    { refreshInterval: 10_000 },
  );
  const { data: settings } = useApi<SettingsResponse>("/settings");

  if (dirErr) {
    return <LoadError error={dirErr} />;
  }

  const job = directional?.job;
  const strategies = directional?.strategies ?? [];
  const underlyings = directional?.underlyings ?? [];
  const enabledNames = (settings?.values.directional_strategies as string[] | undefined) ?? [];
  const symbolMap = (settings?.values.strategy_symbols as Record<string, string[]> | undefined) ?? {};

  /** Write one change through the same settings endpoint the Settings page uses. */
  async function save(key: string, changes: Record<string, unknown>) {
    setSaving(key);
    try {
      await api("/settings", "PATCH", { changes });
      await mutate((k) => typeof k === "string" && (k.startsWith("/settings") || k.startsWith("/strategies")));
    } catch (e) {
      toast.error("Could not save", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving("");
    }
  }

  const toggleStrategy = (name: string, on: boolean) =>
    save(name, {
      directional_strategies: on ? [...new Set([...enabledNames, name])] : enabledNames.filter((n) => n !== name),
    });

  // An unset strategy runs on every underlying, so the first symbol switched off has to be written
  // as "all the others" rather than as an empty list, which the engine reads as no restriction.
  const toggleSymbol = (name: string, symbol: string, on: boolean) => {
    const current = symbolMap[name]?.length ? symbolMap[name] : underlyings;
    const next = on ? [...new Set([...current, symbol])] : current.filter((s) => s !== symbol);
    return save(`${name}:${symbol}`, { strategy_symbols: { ...symbolMap, [name]: next } });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Strategies"
        description="The directional models that trade the perpetuals: which are on, which symbols each runs, and every signal they have fired."
        actions={
          <div className="flex items-center gap-3">
            <Link href="/">
              <Button variant="outline" size="sm" className="gap-2 text-xs">
                <Sparkles className="size-3.5 text-primary" /> Options Desk
              </Button>
            </Link>
            <Link href="/auto">
              <Button size="sm" className="gap-2 text-xs">
                <Bot className="size-3.5" /> Auto Options
              </Button>
            </Link>
          </div>
        }
      />

      {/* Runner Status Banner */}
      {job && (
        <Card className="border-border/60 bg-muted/20">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4 text-xs">
            <div className="flex items-center gap-3">
              <span className="flex size-2 rounded-full bg-emerald-500 animate-pulse" />
              <div>
                <span className="font-semibold text-foreground">{job.label || "Strategy Runner"}</span>
                <span className="mx-2 text-muted-foreground">•</span>
                <span className="text-muted-foreground">Runs {job.schedule}</span>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-4 text-muted-foreground">
              <div>
                Runs: <span className="font-mono font-medium text-foreground">{job.runs}</span>
              </div>
              <div>
                Last Run: <span className="font-mono font-medium text-foreground">{job.last_run ? ago(job.last_run) : "—"}</span>
              </div>
              {job.last_result && (
                <div className="rounded bg-background px-2 py-0.5 font-mono text-[11px] border">
                  {job.last_result}
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Directional Strategies Cards */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-tight text-foreground flex items-center gap-2">
            <Crosshair className="size-4 text-primary" /> Directional Algorithmic Strategies
          </h2>
          <span className="text-xs text-muted-foreground">Evaluated on closed candles</span>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          {strategies.map((strat) => (
            <Card key={strat.name} className={`flex flex-col justify-between ${strat.enabled ? "" : "opacity-60"}`}>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <Badge variant={strat.enabled ? "solid" : "default"}>
                    {strat.enabled ? "Active" : "Disabled"}
                  </Badge>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-muted-foreground font-semibold">
                      {strat.interval}
                    </span>
                    {saving === strat.name ? (
                      <Loader2 className="size-4 animate-spin text-muted-foreground" />
                    ) : (
                      <Switch
                        checked={strat.enabled}
                        onCheckedChange={(on) => toggleStrategy(strat.name, on)}
                        aria-label={`${strat.label} on or off`}
                      />
                    )}
                  </div>
                </div>
                <CardTitle className="text-base font-bold mt-1">{strat.label}</CardTitle>
                <p className="text-xs text-muted-foreground leading-relaxed mt-1">{strat.summary}</p>
              </CardHeader>

              <CardContent className="space-y-3 pt-0 text-xs">
                {/* The second switch: which symbols this strategy may trade. */}
                <div className="rounded-lg border bg-muted/30 p-2.5 space-y-2">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Symbols
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {underlyings.map((symbol) => {
                      const on = strat.symbols.includes(symbol);
                      const key = `${strat.name}:${symbol}`;
                      return (
                        <div key={symbol} className="flex items-center gap-1.5 rounded-md border bg-background px-2 py-1">
                          <span className="font-mono text-[11px] font-semibold">{symbol}</span>
                          {saving === key ? (
                            <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
                          ) : (
                            <Switch
                              checked={on}
                              disabled={!strat.enabled}
                              onCheckedChange={(next) => toggleSymbol(strat.name, symbol, next)}
                              aria-label={`${strat.label} on ${symbol}`}
                              className="h-4 w-7 *:data-[slot=switch-thumb]:size-3 *:data-[slot=switch-thumb]:data-[state=checked]:translate-x-3.5"
                            />
                          )}
                        </div>
                      );
                    })}
                    {underlyings.length === 0 && (
                      <span className="text-[11px] text-muted-foreground">
                        No underlying is enabled.{" "}
                        <Link href="/settings" className="font-medium text-primary hover:underline">Settings</Link>
                      </span>
                    )}
                  </div>
                  {strat.enabled && strat.symbols.length === 0 && underlyings.length > 0 && (
                    <p className="text-[11px] text-loss">On, but switched off for every symbol — it will never fire.</p>
                  )}
                </div>

                <div className="rounded-lg border bg-muted/30 p-2.5 space-y-1.5 text-[11px]">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Stop Rule:</span>
                    <span className="font-medium text-right text-foreground">{strat.stop_rule}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Target Rule:</span>
                    <span className="font-medium text-right text-foreground">{strat.target_rule}</span>
                  </div>
                </div>

                {/* Latest Evaluations */}
                <div className="space-y-2 pt-1 border-t">
                  <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                    Latest Bar Scan
                  </div>
                  {strat.latest?.map((ev, i) => (
                    <div key={i} className="flex items-start justify-between gap-2 text-[11px] rounded bg-muted/20 p-1.5 border">
                      <span className="font-mono font-bold">{ev.symbol}</span>
                      <span className="text-right text-muted-foreground truncate max-w-[200px]" title={ev.reason}>
                        {ev.reason || "Scanning..."}
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {/* Signals stream. The options structure catalog lives on the Options Desk, with the chain
          and the builder that uses it. */}
      <div className="space-y-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold tracking-tight text-foreground">
          <ListFilter className="size-4 text-primary" /> Signals Stream ({signals?.length ?? 0})
        </h2>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-sm font-semibold">Signals Log</CardTitle>
                <p className="text-xs text-muted-foreground">
                  Signals fired by directional strategies with entry, stop-loss, and target levels.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <select
                  value={selectedStrategy}
                  onChange={(e) => setSelectedStrategy(e.target.value)}
                  className="h-8 rounded-md border border-input bg-background px-2.5 py-1 text-xs"
                >
                  <option value="">All Strategies</option>
                  {strategies.map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>
            </CardHeader>
            <CardContent>
              {signals && signals.length > 0 ? (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-28 text-xs">Time</TableHead>
                        <TableHead className="text-xs">Symbol</TableHead>
                        <TableHead className="text-xs">Strategy</TableHead>
                        <TableHead className="text-xs">Side</TableHead>
                        <TableHead className="text-xs">Action</TableHead>
                        <TableHead className="text-xs">Entry</TableHead>
                        <TableHead className="text-xs">Stop</TableHead>
                        <TableHead className="text-xs">Target</TableHead>
                        <TableHead className="text-xs">R:R</TableHead>
                        <TableHead className="text-xs">Reason</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {signals.map((sig) => {
                        const isBuy = sig.side.toLowerCase() === "buy";
                        const isSell = sig.side.toLowerCase() === "sell";
                        return (
                          <TableRow key={sig.id} className="text-xs">
                            <TableCell className="font-mono text-muted-foreground">
                              {ago(sig.ts)}
                            </TableCell>
                            <TableCell className="font-mono font-semibold">{sig.symbol}</TableCell>
                            <TableCell className="font-medium">{sig.strategy}</TableCell>
                            <TableCell>
                              {sig.side ? (
                                <Badge variant={isBuy ? "profit" : isSell ? "loss" : "default"}>
                                  {sig.side.toUpperCase()}
                                </Badge>
                              ) : (
                                "—"
                              )}
                            </TableCell>
                            <TableCell>
                              <Badge
                                variant={
                                  sig.action === "open"
                                    ? "solid"
                                    : sig.action === "error"
                                    ? "loss"
                                    : "outline"
                                }
                                className="text-[10px]"
                              >
                                {sig.action}
                              </Badge>
                            </TableCell>
                            <TableCell className="font-mono">
                              {sig.entry ? `$${num(sig.entry, 2)}` : "—"}
                            </TableCell>
                            <TableCell className="font-mono text-rose-600">
                              {sig.stop ? `$${num(sig.stop, 2)}` : "—"}
                            </TableCell>
                            <TableCell className="font-mono text-emerald-600">
                              {sig.target ? `$${num(sig.target, 2)}` : "—"}
                            </TableCell>
                            <TableCell className="font-mono">
                              {sig.rr ? `${sig.rr.toFixed(1)}:1` : "—"}
                            </TableCell>
                            <TableCell className="text-muted-foreground max-w-xs truncate" title={sig.reason}>
                              {sig.reason}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/80 bg-muted/10 px-6 py-10 text-center">
                  <div className="relative mb-3 flex size-12 items-center justify-center rounded-full bg-primary/10">
                    <Crosshair className="size-6 text-primary animate-pulse" />
                    <span className="absolute -top-0.5 -right-0.5 flex size-3">
                      <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                      <span className="relative inline-flex size-3 rounded-full bg-emerald-500" />
                    </span>
                  </div>
                  <h3 className="text-sm font-semibold text-foreground">
                    Real-Time Algorithmic Scanner Active
                  </h3>
                  <p className="mt-1 max-w-md text-xs text-muted-foreground leading-relaxed">
                    The engine is continuously evaluating closed candles every 20 seconds. Directional signals with automated entries, stops, and targets will log here automatically.
                  </p>

                  <div className="mt-4 grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-3 text-left">
                    <div className="rounded-lg border bg-background/80 p-2.5 shadow-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-[10px] font-bold text-muted-foreground">5m</span>
                        <Badge variant="outline" className="text-[9px] px-1 py-0">SCANNING</Badge>
                      </div>
                      <div className="mt-1 font-semibold text-xs text-foreground">EMA × VWAP</div>
                      <div className="mt-0.5 text-[10px] text-muted-foreground">Waiting for 9 EMA crossover</div>
                    </div>

                    <div className="rounded-lg border bg-background/80 p-2.5 shadow-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-[10px] font-bold text-muted-foreground">15m</span>
                        <Badge variant="outline" className="text-[9px] px-1 py-0">SCANNING</Badge>
                      </div>
                      <div className="mt-1 font-semibold text-xs text-foreground">Put/Call Shift</div>
                      <div className="mt-0.5 text-[10px] text-muted-foreground">Waiting for 0.15+ OI shift</div>
                    </div>

                    <div className="rounded-lg border bg-background/80 p-2.5 shadow-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-[10px] font-bold text-muted-foreground">15m</span>
                        <Badge variant="outline" className="text-[9px] px-1 py-0">SCANNING</Badge>
                      </div>
                      <div className="mt-1 font-semibold text-xs text-foreground">Supertrend</div>
                      <div className="mt-0.5 text-[10px] text-muted-foreground">Waiting for 10,3 flip</div>
                    </div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
      </div>
    </div>
  );
}
