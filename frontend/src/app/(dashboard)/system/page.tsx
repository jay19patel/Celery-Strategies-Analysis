"use client";

import { useState } from "react";
import { useSWRConfig } from "swr";
import { Bell, Bot, Check, Cpu, Landmark, Loader2, Play, Radio, Server, X } from "lucide-react";
import { toast } from "sonner";

import { EventLog } from "@/components/system/event-log";
import { PageHeader } from "@/components/shared/page";
import { KV } from "@/components/shared/stat";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { ago, dateTime, hours, num, price, titleCase, until } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { JobStatus, SystemStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

function Dot({ on }: { on: boolean }) {
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", on ? "bg-profit" : "bg-loss")} />;
}

function Yes({ ok }: { ok: boolean }) {
  return ok ? <Check className="inline size-4 text-profit" /> : <X className="inline size-4 text-loss" />;
}

export default function SystemPage() {
  const { data, error, isLoading } = useApi<SystemStatus>("/system/status", { refreshInterval: 5_000 });
  const { mutate } = useSWRConfig();
  const [running, setRunning] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  const runJob = async (job: JobStatus) => {
    setRunning(job.id);
    try {
      const r = await api<JobStatus>(`/system/jobs/${job.id}/run`, "POST");
      if (r.last_error) toast.error(`${job.label} failed`, { description: r.last_error });
      else toast.success(`${job.label} ran`, { description: r.last_result || `${num(r.last_ms, 0)} ms` });
      await mutate("/system/status");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(null);
    }
  };

  const resume = async (broker: string) => {
    try {
      await api("/portfolio/resume", "POST", { broker });
      toast.success(`${broker} resumed`);
      await mutate("/system/status");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const testModels = async () => {
    setTesting(true);
    try {
      const r = await api<{ ok: boolean; error?: string; model?: string; ms?: number }>("/ai/test", "POST");
      if (r.ok) toast.success("Model answered", { description: `${r.model} in ${r.ms} ms` });
      else toast.error("No model answered", { description: r.error });
      await mutate("/system/status");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="System"
        description={data ? `Engine up ${hours(data.uptime_seconds / 3600)} · started ${dateTime(data.started_at)} · ${data.events.dashboards} dashboard(s) connected` : "Engine health, jobs and the event log"}
      />
      <LoadError error={error} />
      {isLoading && !data && <Loading rows={6} />}
      {data && (
        <>
          <div className="grid gap-5 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>
                  <Radio className="size-4 text-slate-400" /> Market feed
                </CardTitle>
                <Badge variant={data.feed.websocket.connected ? "profit" : "loss"}>{data.feed.websocket.connected ? "live" : "down"}</Badge>
              </CardHeader>
              <CardContent className="space-y-3">
                <div>
                  <div className="mb-1 flex items-center gap-2 text-[12px] font-semibold text-slate-600">
                    <Dot on={data.feed.websocket.connected} /> WebSocket
                  </div>
                  <KV label="Connected since" value={dateTime(data.feed.websocket.since)} />
                  <KV label="Last message" value={ago(data.feed.websocket.last_message)} />
                  <KV label="Reconnects" value={num(data.feed.websocket.reconnects, 0)} />
                  <KV label="Messages" value={num(data.feed.websocket.messages, 0)} />
                  {data.feed.websocket.error && <p className="mt-1 break-words text-xs text-loss">{data.feed.websocket.error}</p>}
                </div>
                <div className="border-t border-slate-100 pt-2">
                  <div className="mb-1 flex items-center gap-2 text-[12px] font-semibold text-slate-600">
                    <Dot on={!data.feed.rest.error && !!data.feed.rest.last_refresh} /> REST
                  </div>
                  <KV label="Last refresh" value={ago(data.feed.rest.last_refresh)} />
                  {data.feed.rest.error && <p className="mt-1 break-words text-xs text-loss">{data.feed.rest.error}</p>}
                </div>
                <div className="border-t border-slate-100 pt-2">
                  {Object.entries(data.feed.prices).map(([sym, p]) => (
                    <KV
                      key={sym}
                      label={
                        <span className="flex items-center gap-1.5">
                          <Dot on={p.fresh} /> {sym}
                        </span>
                      }
                      value={`${price(p.price)} · idx ${price(p.index)} · ${num(p.age_seconds, 1)}s`}
                    />
                  ))}
                  {Object.entries(data.feed.options).map(([u, n]) => (
                    <KV key={u} label={`${u} option quotes`} value={num(n, 0)} />
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>
                  <Landmark className="size-4 text-slate-400" /> Brokers
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {data.brokers.map((b) => (
                  <div key={b.name} className="rounded-lg border p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-semibold">{titleCase(b.name)}</span>
                      <Badge variant={b.active ? "primary" : "default"}>{b.active ? "active" : "inactive"}</Badge>
                      <Badge variant="outline">{b.env}</Badge>
                      {b.real_money && <Badge variant="solid">REAL MONEY</Badge>}
                    </div>
                    <KV label="Ready" value={b.ready ? <Yes ok /> : <span className="text-xs text-loss">{b.not_ready}</span>} />
                    <KV label="Auto-trading" value={b.auto ? "on" : "off"} />
                    {b.halted && (
                      <div className="mt-1 flex items-start justify-between gap-2 rounded-md bg-warn-soft px-2 py-1.5 text-xs text-amber-900">
                        <span>Halted: {b.halted}</span>
                        <Button size="xs" variant="outline" onClick={() => resume(b.name)}>
                          Resume
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>

            <div className="space-y-5">
              <Card>
                <CardHeader>
                  <CardTitle>
                    <Bot className="size-4 text-slate-400" /> AI models
                  </CardTitle>
                  <Button size="xs" variant="outline" onClick={testModels} disabled={testing}>
                    {testing && <Loader2 className="animate-spin" />} Test models
                  </Button>
                </CardHeader>
                <CardContent className="space-y-2">
                  <KV label="AI" value={data.ai.enabled ? "enabled" : "off"} />
                  {data.ai.models.map((m) => (
                    <div key={m.model} className="rounded-md border px-2.5 py-2 text-[12.5px]">
                      <div className="flex items-center justify-between gap-2">
                        <span className="num truncate font-medium">{m.model}</span>
                        <span className="flex items-center gap-1 text-xs">
                          <Yes ok={m.key_present} /> key
                        </span>
                      </div>
                      {!m.key_present && m.missing.length > 0 && <div className="text-xs text-loss">missing {m.missing.join(", ")}</div>}
                      <div className="num mt-0.5 text-xs text-muted-foreground">
                        {num(m.calls ?? 0, 0)} calls · {num(m.failures ?? 0, 0)} failed{m.last_ms ? ` · ${m.last_ms} ms` : ""}
                      </div>
                      {m.last_error && <div className="mt-0.5 break-words text-xs text-loss">{m.last_error}</div>}
                    </div>
                  ))}
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>
                    <Bell className="size-4 text-slate-400" /> Notifications
                  </CardTitle>
                  <span className="text-xs text-muted-foreground">{data.notifications.enabled.length ? data.notifications.enabled.join(", ") : "none enabled"}</span>
                </CardHeader>
                <CardContent>
                  {Object.entries(data.notifications.channels).map(([name, c]) => (
                    <div key={name} className="py-1">
                      <KV label={titleCase(name)} value={`${c.sent} sent · ${c.failed} failed · ${ago(c.last_at)}`} />
                      {c.last_error && <div className="break-words text-xs text-loss">{c.last_error}</div>}
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>
                <Cpu className="size-4 text-slate-400" /> Jobs
              </CardTitle>
              <span className="text-xs text-muted-foreground">APScheduler, inside the engine</span>
            </CardHeader>
            <CardContent className="p-0">
              {data.jobs.length === 0 ? (
                <Empty title="No jobs scheduled">The engine is running without its scheduler.</Empty>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Job</TableHead>
                      <TableHead>Schedule</TableHead>
                      <TableHead className="text-right">Runs</TableHead>
                      <TableHead className="text-right">Failures</TableHead>
                      <TableHead>Last run</TableHead>
                      <TableHead className="text-right">Duration</TableHead>
                      <TableHead>Result</TableHead>
                      <TableHead>Next</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.jobs.map((j) => (
                      <TableRow key={j.id}>
                        <TableCell className="font-medium">{j.label}</TableCell>
                        <TableCell className="text-muted-foreground">{j.schedule}</TableCell>
                        <TableCell className="num text-right">{num(j.runs, 0)}</TableCell>
                        <TableCell className={cn("num text-right", j.failures ? "text-loss" : "")}>{num(j.failures, 0)}</TableCell>
                        <TableCell className="text-muted-foreground">{ago(j.last_run)}</TableCell>
                        <TableCell className="num text-right">{j.last_ms === null ? "—" : `${num(j.last_ms, 0)} ms`}</TableCell>
                        <TableCell className="max-w-[340px] truncate whitespace-normal">
                          {j.last_error ? <span className="text-xs text-loss">{j.last_error}</span> : <span className="text-xs text-muted-foreground">{j.last_result || "—"}</span>}
                        </TableCell>
                        <TableCell className="text-muted-foreground">{until(j.next_run)}</TableCell>
                        <TableCell className="text-right">
                          <Button size="xs" variant="outline" onClick={() => runJob(j)} disabled={running === j.id}>
                            {running === j.id ? <Loader2 className="animate-spin" /> : <Play />} Run
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                <Server className="size-4 text-slate-400" /> Delta API calls
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {Object.keys({ ...data.feed.rest_calls, ...data.delta_api }).length === 0 ? (
                <Empty title="No calls yet" />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Client</TableHead>
                      <TableHead>Endpoint</TableHead>
                      <TableHead className="text-right">Calls</TableHead>
                      <TableHead className="text-right">Errors</TableHead>
                      <TableHead className="text-right">Last</TableHead>
                      <TableHead>Last error</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(
                      [
                        ["market data", data.feed.rest_calls],
                        ["trading", data.delta_api],
                      ] as const
                    ).flatMap(([client, calls]) =>
                      Object.entries(calls).map(([endpoint, c]) => (
                        <TableRow key={`${client}-${endpoint}`}>
                          <TableCell className="text-muted-foreground">{client}</TableCell>
                          <TableCell className="num text-xs">{endpoint}</TableCell>
                          <TableCell className="num text-right">{num(c.count, 0)}</TableCell>
                          <TableCell className={cn("num text-right", c.errors ? "text-loss" : "")}>{num(c.errors, 0)}</TableCell>
                          <TableCell className="num text-right">{num(c.last_ms, 0)} ms</TableCell>
                          <TableCell className="max-w-[360px] truncate text-xs text-loss">{c.last_error}</TableCell>
                        </TableRow>
                      )),
                    )}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}
      <EventLog />
    </>
  );
}
