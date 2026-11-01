"use client";

import { useState } from "react";
import Link from "next/link";
import { Bot, Crosshair, Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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
          <>
            <Button asChild variant="outline" size="sm">
              <Link href="/">
                <Sparkles className="text-primary" /> Options Desk
              </Link>
            </Button>
            <Button asChild variant="outline" size="sm">
              <Link href="/auto">
                <Bot /> Auto Options
              </Link>
            </Button>
          </>
        }
      />

      {/* Runner Status Banner */}
      {job && (
        <Card>
          <CardContent className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs">
            <div className="flex items-center gap-3">
              <span className="size-2 shrink-0 rounded-full bg-profit" />
              <div>
                <span className="font-semibold text-foreground">{job.label || "Strategy Runner"}</span>
                <span className="mx-2 text-muted-foreground">•</span>
                <span className="text-muted-foreground">Runs {job.schedule}</span>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-4 text-muted-foreground">
              <div>
                Runs <span className="num font-medium text-foreground">{job.runs}</span>
              </div>
              <div>
                Last run <span className="num font-medium text-foreground">{job.last_run ? ago(job.last_run) : "—"}</span>
              </div>
              {job.last_result && (
                <div className="num rounded-md border bg-muted px-2 py-0.5 text-[11px]">
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
            <Crosshair className="size-4 text-primary" /> Directional strategies
          </h2>
          <span className="text-xs text-muted-foreground">Evaluated on closed candles</span>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          {strategies.map((strat) => (
            <Card key={strat.name} className="flex flex-col">
              <CardHeader className="flex-col items-stretch gap-1.5 border-b-0 pb-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant={strat.enabled ? "profit" : "default"}>{strat.enabled ? "On" : "Off"}</Badge>
                    <span className="num text-xs font-medium text-muted-foreground">{strat.interval}</span>
                  </div>
                  <div className="flex items-center gap-2">
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
                <CardTitle className={`mt-1 text-[15px] ${strat.enabled ? "" : "text-muted-foreground"}`}>{strat.label}</CardTitle>
                <p className="text-xs leading-relaxed text-muted-foreground">{strat.summary}</p>
              </CardHeader>

              <CardContent className="mt-auto space-y-3 pt-0 text-xs">
                {/* The second switch: which symbols this strategy may trade. */}
                <div className="space-y-2 rounded-lg bg-secondary/60 p-2.5">
                  <div className="text-[11px] font-medium text-muted-foreground">Symbols</div>
                  <div className="flex flex-wrap gap-2">
                    {underlyings.map((symbol) => {
                      const on = strat.symbols.includes(symbol);
                      const key = `${strat.name}:${symbol}`;
                      return (
                        <div key={symbol} className="flex items-center gap-1.5 rounded-md border bg-background px-2 py-1">
                          <span className="num text-[11px] font-semibold">{symbol}</span>
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

                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 rounded-lg bg-secondary/60 p-2.5 text-[11px]">
                  <dt className="text-muted-foreground">Stop</dt>
                  <dd className="text-right font-medium text-foreground">{strat.stop_rule}</dd>
                  <dt className="text-muted-foreground">Target</dt>
                  <dd className="text-right font-medium text-foreground">{strat.target_rule}</dd>
                </dl>

                {/* Latest Evaluations */}
                <div className="space-y-1.5 border-t pt-3">
                  <div className="text-[11px] font-medium text-muted-foreground">Latest bar</div>
                  {strat.latest?.map((ev, i) => (
                    <div key={i} className="flex items-start gap-3 rounded-md border px-2 py-1.5 text-[11px]">
                      <span className="num shrink-0 font-semibold">{ev.symbol}</span>
                      <span className="ml-auto line-clamp-2 text-right text-muted-foreground" title={ev.reason}>
                        {ev.reason || "Waiting for the next closed bar"}
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
      <Card>
            <CardHeader>
              <div className="space-y-1">
                <CardTitle>
                  Signals <span className="num font-normal text-muted-foreground">{signals?.length ?? 0}</span>
                </CardTitle>
                <p className="text-xs text-muted-foreground">Every signal a directional strategy fired, with its entry, stop and target.</p>
              </div>
              <Select value={selectedStrategy || "all"} onValueChange={(v) => setSelectedStrategy(v === "all" ? "" : v)}>
                <SelectTrigger size="sm" className="text-xs" aria-label="Filter by strategy">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end">
                  <SelectItem value="all">All strategies</SelectItem>
                  {strategies.map((s) => (
                    <SelectItem key={s.name} value={s.name}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </CardHeader>
            <CardContent className={signals && signals.length > 0 ? "p-0" : undefined}>
              {signals && signals.length > 0 ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-28">Time</TableHead>
                        <TableHead>Symbol</TableHead>
                        <TableHead>Strategy</TableHead>
                        <TableHead>Side</TableHead>
                        <TableHead>Action</TableHead>
                        <TableHead>Entry</TableHead>
                        <TableHead>Stop</TableHead>
                        <TableHead>Target</TableHead>
                        <TableHead>R:R</TableHead>
                        <TableHead>Reason</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {signals.map((sig) => {
                        const isBuy = sig.side.toLowerCase() === "buy";
                        const isSell = sig.side.toLowerCase() === "sell";
                        return (
                          <TableRow key={sig.id}>
                            <TableCell className="num text-muted-foreground">
                              {ago(sig.ts)}
                            </TableCell>
                            <TableCell className="num font-semibold">{sig.symbol}</TableCell>
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
                                    ? "primary"
                                    : sig.action === "error"
                                    ? "loss"
                                    : "outline"
                                }
                              >
                                {sig.action}
                              </Badge>
                            </TableCell>
                            <TableCell className="num">
                              {sig.entry ? `$${num(sig.entry, 2)}` : "—"}
                            </TableCell>
                            <TableCell className="num text-loss">
                              {sig.stop ? `$${num(sig.stop, 2)}` : "—"}
                            </TableCell>
                            <TableCell className="num text-profit">
                              {sig.target ? `$${num(sig.target, 2)}` : "—"}
                            </TableCell>
                            <TableCell className="num">
                              {sig.rr ? `${sig.rr.toFixed(1)}:1` : "—"}
                            </TableCell>
                            <TableCell className="max-w-xs truncate text-muted-foreground" title={sig.reason}>
                              {sig.reason}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
              ) : (
                <Empty title="No signals yet">
                  Each enabled strategy checks every closed candle; a signal it fires logs here with its entry, stop and target.
                </Empty>
              )}
            </CardContent>
      </Card>
    </div>
  );
}
