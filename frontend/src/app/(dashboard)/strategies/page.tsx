"use client";

// Every strategy the engine found in backend/app/strategies, one card each, drawn entirely from
// GET /strategies: adding or deleting a strategy file changes this page without touching it.

import { useState } from "react";
import { Crosshair, Layers, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";

import { ParamsForm } from "@/components/strategies/params-form";
import { Confirm } from "@/components/shared/confirm";
import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError, Loading, Notice } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { ago, num } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { JobStatus, SignalRow, StrategiesResponse, StrategyConfig, StrategyInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

const VERDICT = {
  unproven: { tone: "default", text: "unproven" },
  positive: { tone: "profit", text: "proven edge" },
  negative: { tone: "loss", text: "proven losing" },
  unclear: { tone: "warn", text: "not proven either way" },
} as const;

function JobLine({ label, job }: { label: string; job: JobStatus | null }) {
  if (!job) return null;
  const failing = Boolean(job.last_error);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      <span className={cn("size-2 shrink-0 rounded-full", failing ? "bg-loss" : job.last_run ? "bg-profit" : "bg-slate-300")} />
      <span className="font-semibold">{label}</span>
      <span className="text-muted-foreground">{job.schedule}</span>
      <span className="text-muted-foreground">last run {job.last_run ? ago(job.last_run) : "never"}</span>
      {failing ? <span className="text-loss">{job.last_error}</span> : job.last_result && <span className="num text-muted-foreground">{job.last_result}</span>}
    </div>
  );
}

export default function StrategiesPage() {
  const { mutate } = useSWRConfig();
  const [filter, setFilter] = useState("");
  const [saving, setSaving] = useState("");
  const { data, error, isLoading } = useApi<StrategiesResponse>("/strategies", { refreshInterval: 15_000 });
  const { data: signals, error: signalsError } = useApi<SignalRow[]>(`/strategies/signals${filter ? `?strategy=${filter}` : ""}`, { refreshInterval: 15_000 });

  async function change(name: string, body: Partial<StrategyConfig>, what: string) {
    setSaving(`${name}:${what}`);
    try {
      await api(`/strategies/${name}`, "PATCH", body);
      await mutate((k) => typeof k === "string" && (k.startsWith("/strategies") || k.startsWith("/settings")));
    } catch (e) {
      toast.error("Not saved", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving("");
    }
  }

  const strategies = data?.strategies ?? [];
  const delta = data?.brokers.find((b) => b.name === "delta");
  const perps = strategies.filter((s) => s.family === "perp");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Strategies"
        description="Every strategy the engine runs, one file each. Switch one on, choose its symbols and numbers, and decide whether it may trade real money."
      />
      <LoadError error={error} />
      {isLoading && !data && <Loading rows={6} />}

      {data && (
        <Card>
          <CardContent className="space-y-2 py-3">
            <JobLine label="Perpetual strategies" job={data.jobs.perp} />
            <JobLine label="Option strategies" job={data.jobs.options} />
          </CardContent>
        </Card>
      )}

      {(["perp", "options"] as const).map((family) => {
        const rows = strategies.filter((s) => s.family === family);
        if (!rows.length) return null;
        return (
          <section key={family} className="space-y-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              {family === "perp" ? <Crosshair className="size-4 text-primary" /> : <Layers className="size-4 text-primary" />}
              {family === "perp" ? "Perpetual futures" : "Option structures"}
            </h2>
            <div className="grid gap-4 lg:grid-cols-2">
              {rows.map((s) => (
                <StrategyCard
                  key={s.name}
                  s={s}
                  underlyings={data?.underlyings ?? []}
                  deltaReady={Boolean(delta?.active && delta.ready)}
                  saving={saving}
                  change={change}
                />
              ))}
            </div>
          </section>
        );
      })}

      {perps.length > 0 && (
        <Card>
          <CardHeader>
            <div className="space-y-1">
              <CardTitle>
                Signals <span className="num font-normal text-muted-foreground">{signals?.length ?? 0}</span>
              </CardTitle>
              <p className="text-xs text-muted-foreground">Every position a perpetual strategy opened, and every error, with why.</p>
            </div>
            <Select value={filter || "all"} onValueChange={(v) => setFilter(v === "all" ? "" : v)}>
              <SelectTrigger size="sm" className="text-xs" aria-label="Filter by strategy">
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="all">All strategies</SelectItem>
                {perps.map((s) => (
                  <SelectItem key={s.name} value={s.name}>{s.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent className={signals && signals.length > 0 ? "p-0" : undefined}>
            <LoadError error={signalsError} />
            {signals && signals.length > 0 ? (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-24">When</TableHead>
                      <TableHead>Symbol</TableHead>
                      <TableHead>Strategy</TableHead>
                      <TableHead>Side</TableHead>
                      <TableHead>Action</TableHead>
                      <TableHead>Entry</TableHead>
                      <TableHead>Stop</TableHead>
                      <TableHead>Target</TableHead>
                      <TableHead>Why</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {signals.map((sig) => (
                      <TableRow key={sig.id}>
                        <TableCell className="num text-muted-foreground">{ago(sig.ts)}</TableCell>
                        <TableCell className="num font-semibold">{sig.symbol}</TableCell>
                        <TableCell>{strategies.find((s) => s.name === sig.strategy)?.label ?? sig.strategy}</TableCell>
                        <TableCell>{sig.side ? <Badge variant={sig.side === "buy" ? "profit" : "loss"}>{sig.side}</Badge> : "—"}</TableCell>
                        <TableCell><Badge variant={sig.action === "open" ? "primary" : sig.action === "error" ? "loss" : "outline"}>{sig.action}</Badge></TableCell>
                        <TableCell className="num">{sig.entry ? num(sig.entry, 2) : "—"}</TableCell>
                        <TableCell className="num text-loss">{sig.stop ? num(sig.stop, 2) : "—"}</TableCell>
                        <TableCell className="num text-profit">{sig.target ? num(sig.target, 2) : "—"}</TableCell>
                        <TableCell className="min-w-64 whitespace-normal text-muted-foreground">{sig.reason}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <Empty title="No signals yet">A perpetual strategy checks every closed bar; when one opens a position, it is listed here with its reason.</Empty>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function StrategyCard({
  s, underlyings, deltaReady, saving, change,
}: {
  s: StrategyInfo;
  underlyings: string[];
  deltaReady: boolean;
  saving: string;
  change: (name: string, body: Partial<StrategyConfig>, what: string) => Promise<void>;
}) {
  const on = s.config.enabled;
  const verdict = VERDICT[s.record.verdict];
  const busy = (what: string) => saving === `${s.name}:${what}`;

  // No restriction is stored as an empty list: switching one symbol off writes "all the others".
  const toggleSymbol = (u: string, next: boolean) => {
    const current = s.config.underlyings.length ? s.config.underlyings : underlyings;
    const list = next ? [...new Set([...current, u])] : current.filter((x) => x !== u);
    return change(s.name, { underlyings: list }, `symbol:${u}`);
  };

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex-col items-stretch gap-2 border-b-0 pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className={cn("text-[15px]", !on && "text-muted-foreground")}>{s.label}</CardTitle>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{s.summary}</p>
          </div>
          {busy("enabled") ? (
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          ) : (
            <Switch checked={on} onCheckedChange={(v) => change(s.name, { enabled: v }, "enabled")} aria-label={`${s.label} on or off`} />
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={on ? "profit" : "default"}>{on ? "on" : "off"}</Badge>
          <Badge variant={s.config.live ? "solid" : "outline"}>{s.config.live ? "paper + live" : "paper only"}</Badge>
          {s.interval && <Badge variant="outline" className="num">{s.interval}</Badge>}
          {s.risk && <Badge variant={s.risk === "defined" ? "primary" : "warn"}>{s.risk} risk</Badge>}
          <Badge variant={verdict.tone}>{verdict.text}</Badge>
          <span className="text-[11px] text-muted-foreground">v{s.version}</span>
        </div>
      </CardHeader>

      <CardContent className="mt-auto space-y-3 pt-0 text-xs">
        <div className="grid grid-cols-3 gap-2 rounded-lg bg-secondary/60 p-2.5 text-[11px]">
          <div>
            <div className="text-muted-foreground">Automatic trades</div>
            <div className="num font-semibold">{s.record.trades_r}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Average per trade</div>
            <div className="num font-semibold">{s.record.expectancy_r == null ? "—" : `${s.record.expectancy_r.toFixed(2)}R`}</div>
          </div>
          <div>
            <div className="text-muted-foreground">95% range</div>
            <div className="num font-semibold">
              {s.record.ci_low_r == null ? "—" : `${s.record.ci_low_r.toFixed(2)} to ${(s.record.ci_high_r ?? 0).toFixed(2)}R`}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-medium text-muted-foreground">Symbols</span>
          {underlyings.map((u) => (
            <label key={u} className="flex items-center gap-1.5 rounded-md border bg-background px-2 py-1">
              <span className="num text-[11px] font-semibold">{u}</span>
              {busy(`symbol:${u}`) ? (
                <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
              ) : (
                <Switch
                  checked={s.underlyings_on.includes(u)}
                  disabled={!on}
                  onCheckedChange={(v) => toggleSymbol(u, v)}
                  aria-label={`${s.label} on ${u}`}
                  className="h-4 w-7 *:data-[slot=switch-thumb]:size-3 *:data-[slot=switch-thumb]:data-[state=checked]:translate-x-3.5"
                />
              )}
            </label>
          ))}
        </div>
        {on && s.underlyings_on.length === 0 && <Notice tone="loss">On, but off for every symbol: it will never trade.</Notice>}

        <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
          <div className="min-w-0">
            <div className="text-[12.5px] font-medium">Trade on Delta too</div>
            <p className="text-[11px] text-muted-foreground">
              Still needs automatic entries on Delta switched on. On real money it gets no size until paper proves an edge.
            </p>
          </div>
          {s.config.live ? (
            <Switch checked onCheckedChange={() => change(s.name, { live: false }, "live")} aria-label={`${s.label} paper only`} />
          ) : (
            <Confirm
              title={`Let ${s.label} trade on Delta?`}
              description={`With automatic entries on Delta switched on, ${s.label} opens positions on your Delta account without asking.${deltaReady ? "" : " Delta is not active yet, so nothing changes until it is."}`}
              confirmLabel="Allow Delta"
              destructive
              onConfirm={() => change(s.name, { live: true }, "live")}
              trigger={(open) => <Switch checked={false} onCheckedChange={() => open()} aria-label={`${s.label} on Delta`} />}
            />
          )}
        </div>

        {(s.stop_rule || s.target_rule) && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-lg bg-secondary/60 p-2.5 text-[11px]">
            <dt className="text-muted-foreground">Stop</dt>
            <dd className="text-right font-medium">{s.stop_rule}</dd>
            <dt className="text-muted-foreground">Target</dt>
            <dd className="text-right font-medium">{s.target_rule}</dd>
          </dl>
        )}

        <details className="rounded-lg border px-3 py-2">
          <summary className="cursor-pointer select-none text-[12.5px] font-medium">Settings</summary>
          <div className="mt-3">
            <ParamsForm
              idPrefix={s.name}
              schema={s.schema}
              values={s.params}
              disabled={busy("params")}
              onChange={(name, value) => change(s.name, { params: { [name]: value } }, "params")}
            />
          </div>
        </details>

        {s.family === "perp" && s.latest.length > 0 && (
          <div className="space-y-1.5 border-t pt-3">
            <div className="text-[11px] font-medium text-muted-foreground">Last closed bar</div>
            {s.latest.map((ev, i) => (
              <div key={i} className="flex items-start gap-3 rounded-md border px-2 py-1.5 text-[11px]">
                <span className="num shrink-0 font-semibold">{ev.symbol}</span>
                <span className="ml-auto text-right text-muted-foreground">{ev.reason}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
