"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Bot,
  Crosshair,
  ExternalLink,
  Layers,
  ListFilter,
  Sparkles,
} from "lucide-react";
import { useSWRConfig } from "swr";

import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ago, num } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { DirectionalStatus, SignalRow, StrategySpec } from "@/lib/types";

export default function StrategiesPage() {
  const { mutate } = useSWRConfig();
  const [selectedStrategy, setSelectedStrategy] = useState<string>("");

  const { data: directional, error: dirErr } = useApi<DirectionalStatus>("/strategies/directional", {
    refreshInterval: 10_000,
  });
  const { data: signals } = useApi<SignalRow[]>(
    `/strategies/signals${selectedStrategy ? `?strategy=${selectedStrategy}` : ""}`,
    { refreshInterval: 10_000 },
  );
  const { data: optionsCatalog } = useApi<StrategySpec[]>("/strategies", {
    revalidateOnFocus: false,
  });

  if (dirErr) {
    return <LoadError error={dirErr} />;
  }

  const job = directional?.job;
  const strategies = directional?.strategies ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Strategies"
        description="Algorithmic directional models on perpetuals, live signals stream, and options structure catalog."
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
            <Card key={strat.name} className="flex flex-col justify-between">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <Badge variant={strat.enabled ? "solid" : "default"}>
                    {strat.enabled ? "Active" : "Disabled"}
                  </Badge>
                  <span className="font-mono text-xs text-muted-foreground font-semibold">
                    {strat.interval}
                  </span>
                </div>
                <CardTitle className="text-base font-bold mt-1">{strat.label}</CardTitle>
                <p className="text-xs text-muted-foreground leading-relaxed mt-1">{strat.summary}</p>
              </CardHeader>

              <CardContent className="space-y-3 pt-0 text-xs">
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

      {/* Tabs for Signals Stream and Options Strategies */}
      <Tabs defaultValue="signals" className="space-y-4">
        <TabsList className="grid w-full grid-cols-2 sm:w-auto">
          <TabsTrigger value="signals" className="gap-2 text-xs">
            <ListFilter className="size-3.5" /> Signals Stream ({signals?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="catalog" className="gap-2 text-xs">
            <Layers className="size-3.5" /> Options Catalog ({optionsCatalog?.length ?? 0})
          </TabsTrigger>
        </TabsList>

        {/* Signals Stream Tab */}
        <TabsContent value="signals" className="space-y-4">
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
                <div className="py-8 text-center">
                  <Empty title="No directional signals have been recorded yet. They will appear here when strategies detect setups." />
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Options Catalog Tab */}
        <TabsContent value="catalog" className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {optionsCatalog?.map((spec) => (
              <Card key={spec.key} className="flex flex-col justify-between">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <Badge
                      variant={
                        spec.view === "bullish"
                          ? "profit"
                          : spec.view === "bearish"
                          ? "loss"
                          : spec.view === "volatile"
                          ? "solid"
                          : "default"
                      }
                      className="text-[10px] capitalize"
                    >
                      {spec.view}
                    </Badge>
                    <div className="flex items-center gap-1.5">
                      <Badge variant="outline" className="text-[10px] uppercase font-mono">
                        {spec.premium}
                      </Badge>
                      <Badge variant="outline" className="text-[10px] capitalize font-mono">
                        {spec.risk} risk
                      </Badge>
                    </div>
                  </div>
                  <CardTitle className="text-base font-bold mt-2">{spec.label}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4 pt-0 text-xs">
                  <p className="text-muted-foreground leading-relaxed">{spec.summary}</p>
                  <div className="border-t pt-3 flex justify-end">
                    <Link href={`/?strategy=${spec.key}`}>
                      <Button variant="outline" size="sm" className="gap-1.5 text-xs">
                        Configure on Desk <ExternalLink className="size-3" />
                      </Button>
                    </Link>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          {(!optionsCatalog || optionsCatalog.length === 0) && (
            <Card>
              <CardContent className="py-8">
                <Empty title="No options strategies in catalog." />
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
