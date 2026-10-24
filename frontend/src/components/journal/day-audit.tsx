"use client";

import { useState } from "react";
import { AlertTriangle, BarChart2, Ban, CheckCircle2, Clock, Cpu, ListChecks, Send, Sparkles } from "lucide-react";

import { dayStart, reasonKind } from "@/components/performance/derive";
import { Pnl } from "@/components/shared/money";
import { Empty, LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { pct, price, signed, time, titleCase, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { Decision, EventRow, JournalDay, OrderRecord, Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

const ORDER_TONE = { filled: "profit", failed: "loss", unknown: "warn", pending: "default" } as const;
const LEVEL_TONE = { info: "default", warning: "warn", error: "loss" } as const;

interface SkipGroup {
  reason: string;
  count: number;
  underlyings: string[];
  first: number;
  last: number;
}

/** Skips with the same reason, numbers aside, are one line: "no strategy passed" forty times is one finding. */
function groupSkips(decisions: Decision[]): SkipGroup[] {
  const groups = new Map<string, SkipGroup>();
  for (const d of decisions.filter((x) => x.action !== "open")) {
    const key = d.reason.replace(/[-+]?\d[\d.,]*/g, "#").slice(0, 140);
    const g = groups.get(key) ?? { reason: d.reason, count: 0, underlyings: [], first: d.ts, last: d.ts };
    g.count += 1;
    if (!g.underlyings.includes(d.underlying)) g.underlyings.push(d.underlying);
    g.first = Math.min(g.first, d.ts);
    g.last = Math.max(g.last, d.ts);
    groups.set(key, g);
  }
  return [...groups.values()].sort((a, b) => b.count - a.count);
}

interface Brief {
  headline: string;
  orders: string;
  risk: string;
  auto: string;
  system: string;
  next: string;
}

/** The day in a few sentences, written by rules from the numbers — no model involved. */
function brief(closed: Structure[], orders: OrderRecord[], decisions: Decision[], events: EventRow[], skips: SkipGroup[]): Brief {
  const net = closed.reduce((a, s) => a + (s.pnl ?? 0), 0);
  const wins = closed.filter((s) => (s.pnl ?? 0) > 0).length;
  const filled = orders.filter((o) => o.status === "filled").length;
  const failed = orders.filter((o) => o.status === "failed").length;
  const unknown = orders.filter((o) => o.status === "unknown").length;
  const kinds = closed.reduce<Record<string, number>>((m, s) => ({ ...m, [reasonKind(s.close_reason)]: (m[reasonKind(s.close_reason)] ?? 0) + 1 }), {});
  const halts = events.filter((e) => e.type === "RiskHalt");
  const problems = events.filter((e) => e.level !== "info");
  const opened = decisions.filter((d) => d.action === "open").length;
  const errors = decisions.filter((d) => d.action === "error").length;

  const headline = !closed.length && !decisions.length && !orders.length
    ? "A quiet day: nothing was opened, closed or decided."
    : closed.length
      ? `${net >= 0 ? "A green" : "A red"} day: ${signed(net)} realised over ${closed.length} closed structure${closed.length === 1 ? "" : "s"} (${wins} won, ${closed.length - wins} lost).`
      : `No structure closed; ${orders.length} leg order${orders.length === 1 ? " was" : "s were"} sent and the auto-trader decided ${decisions.length} time${decisions.length === 1 ? "" : "s"}.`;
  const exitText = Object.entries(kinds).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(", ");
  let next = "Nothing needs fixing.";
  if (unknown) next = `${unknown} order${unknown === 1 ? " has" : "s have"} an unknown outcome: check the exchange, then close the structure from Positions.`;
  else if (failed) next = "Some leg orders failed: open the Orders tab to see which limits the book would not fill.";
  else if (halts.length) next = "The daily loss limit fired: review what drove the loss before resuming the broker.";
  else if (skips[0] && !opened) next = `The auto-trader stood aside all day, mostly because: ${skips[0].reason.slice(0, 160)}`;

  return {
    headline,
    orders: orders.length ? `${orders.length} leg orders: ${filled} filled, ${failed} failed${unknown ? `, ${unknown} with an unknown outcome` : ""}.` : "No orders were sent.",
    risk: halts.length
      ? `The daily loss limit halted ${halts.map((h) => String(h.data?.broker ?? "")).join(", ")}. ${exitText ? `Exits: ${exitText}.` : ""}`
      : exitText ? `Exits: ${exitText}. No loss limit was hit.` : "No exits, and no loss limit was hit.",
    auto: decisions.length
      ? `${decisions.length} auto decisions: ${opened} opened, ${decisions.length - opened - errors} skipped${errors ? `, ${errors} errors` : ""}.`
      : "The auto-trader made no decision.",
    system: problems.length
      ? `${problems.length} warning or error event${problems.length === 1 ? "" : "s"}, the first ${problems[problems.length - 1].type} at ${time(problems[problems.length - 1].ts)}.`
      : "No warnings or errors were recorded.",
    next,
  };
}

function Kpi({ label, value, hint, className }: { label: string; value: React.ReactNode; hint: React.ReactNode; className?: string }) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="mb-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">{label}</div>
      <div className={cn("num text-2xl font-bold text-slate-800", className)}>{value}</div>
      <div className="mt-1 text-[11px] text-slate-400">{hint}</div>
    </div>
  );
}

function Count({ n }: { n: number }) {
  return <span className="num rounded bg-slate-100 px-1.5 text-[10.5px] text-slate-600">{n}</span>;
}

export function DayAudit({ date, onClose }: { date: string | null; onClose: () => void }) {
  const [tab, setTab] = useState("overview");
  const { data, error, isLoading } = useApi<JournalDay>(date ? `/portfolio/journal/day?date=${date}` : null);
  const { data: allOrders } = useApi<OrderRecord[]>(date ? "/trading/orders?limit=2000" : null);

  const start = data && date ? dayStart(date, data.timezone) : 0;
  const orders = (allOrders ?? []).filter((o) => o.created_at >= start && o.created_at < start + 86_400).sort((a, b) => a.created_at - b.created_at);
  const closed = (data?.structures ?? []).filter((s) => s.status === "closed" && s.closed_at && s.closed_at >= start && s.closed_at < start + 86_400);
  const decisions = data?.decisions ?? [];
  const events = data ? [...data.events].sort((a, b) => a.ts - b.ts) : [];
  const problems = events.filter((e) => e.level !== "info");
  const skips = groupSkips(decisions);
  const grossProfit = closed.reduce((a, s) => a + Math.max(0, s.pnl ?? 0), 0);
  const grossLoss = closed.reduce((a, s) => a + Math.min(0, s.pnl ?? 0), 0);
  const wins = closed.filter((s) => (s.pnl ?? 0) > 0).length;
  const story = data ? brief(closed, orders, decisions, events, skips) : null;

  const byStrategy = Object.values(
    closed.reduce<Record<string, { strategy: string; pnl: number; trades: number; wins: number; fees: number }>>((m, s) => {
      const g = m[s.strategy] ?? { strategy: s.strategy, pnl: 0, trades: 0, wins: 0, fees: 0 };
      g.pnl += s.pnl ?? 0;
      g.trades += 1;
      g.wins += (s.pnl ?? 0) > 0 ? 1 : 0;
      g.fees += s.fees;
      return { ...m, [s.strategy]: g };
    }, {}),
  ).sort((a, b) => b.pnl - a.pnl);
  const maxAbs = Math.max(...byStrategy.map((g) => Math.abs(g.pnl)), 1e-9);
  const human = date ? new Date(`${date}T00:00:00`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" }) : "";

  return (
    <Dialog open={!!date} onOpenChange={(o) => { if (!o) { onClose(); setTab("overview"); } }}>
      <DialogContent className="max-w-5xl gap-0 p-0 sm:max-w-5xl">
        <DialogHeader className="flex-row items-center gap-3 border-b bg-slate-50 px-6 py-4">
          <div className="grid size-10 shrink-0 place-items-center rounded-xl border border-blue-100 bg-accent text-primary">
            <BarChart2 className="size-5" />
          </div>
          <div className="min-w-0">
            <DialogTitle className="text-lg">Session audit — {human}</DialogTitle>
            <DialogDescription>{date} · all times {data?.timezone ?? "…"}</DialogDescription>
          </div>
        </DialogHeader>
        <LoadError error={error} className="m-6" />
        {isLoading && !data && <Loading rows={6} className="p-6" />}
        {data && story && (
          <Tabs value={tab} onValueChange={setTab} className="gap-0">
            <div className="overflow-x-auto border-b px-6">
              <TabsList className="h-11 rounded-none bg-transparent p-0">
                {[
                  ["overview", <Sparkles key="i" />, "Overview", null],
                  ["timeline", <Clock key="i" />, "Timeline & skips", events.length],
                  ["orders", <Send key="i" />, "Orders", orders.length],
                  ["system", <Cpu key="i" />, "System health", problems.length],
                  ["trades", <ListChecks key="i" />, "Closed trades", closed.length],
                ].map(([value, icon, label, n]) => (
                  <TabsTrigger
                    key={String(value)}
                    value={String(value)}
                    className="h-full flex-none rounded-none border-0 border-b-2 border-transparent px-3 data-[state=active]:border-primary data-[state=active]:text-primary data-[state=active]:shadow-none"
                  >
                    {icon} {label} {n !== null && <Count n={Number(n)} />}
                  </TabsTrigger>
                ))}
              </TabsList>
            </div>
            <div className="max-h-[68vh] overflow-y-auto bg-slate-50/50 p-6">
              <TabsContent value="overview" className="space-y-6">
                <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                  <Kpi
                    label="Net realised P&L"
                    value={<Pnl value={data.pnl} />}
                    hint={<span className="flex justify-between gap-2"><span>Gross profit <b className="text-profit">{signed(grossProfit)}</b></span><span>loss <b className="text-loss">{signed(grossLoss)}</b></span></span>}
                  />
                  <Kpi label="Win rate" value={closed.length ? pct((100 * wins) / closed.length, 0) : "—"} hint={`${wins} wins / ${closed.length - wins} losses (${closed.length} closed)`} />
                  <Kpi
                    label="Orders dispatched"
                    value={orders.length}
                    hint={`${orders.filter((o) => o.status === "filled").length} filled, ${orders.filter((o) => o.status === "failed").length} failed`}
                  />
                  <Kpi
                    label="Auto decisions"
                    value={decisions.length}
                    className="text-primary"
                    hint={`${decisions.filter((d) => d.action === "open").length} opened · ${decisions.filter((d) => d.action === "skip").length} skipped`}
                  />
                </div>

                <div className="overflow-hidden rounded-xl border border-l-4 border-indigo-100 border-l-indigo-600 bg-card">
                  <div className="flex items-center justify-between border-b border-indigo-100 bg-indigo-50/60 px-5 py-3">
                    <div className="flex items-center gap-2 text-sm font-bold text-indigo-900">
                      <Sparkles className="size-4 text-indigo-600" /> The day in brief
                      <span className="text-[10px] font-normal text-slate-400">written by rules from the numbers</span>
                    </div>
                  </div>
                  <div className="space-y-3 p-5 text-[13px]">
                    <div className="font-semibold leading-snug text-slate-900">{story.headline}</div>
                    {[["Orders", story.orders], ["Risk", story.risk], ["Auto-trader", story.auto], ["System", story.system]].map(([k, v]) => (
                      <div key={k} className="rounded-lg border border-slate-100 bg-slate-50 p-3 leading-relaxed text-slate-600"><b>{k}:</b> {v}</div>
                    ))}
                    <div className="flex items-start gap-1.5 border-t border-slate-100 pt-2 text-xs font-medium text-indigo-800">
                      <CheckCircle2 className="mt-px size-4 shrink-0 text-indigo-600" /> <span><b>Next:</b> {story.next}</span>
                    </div>
                  </div>
                </div>

                <div className="rounded-xl border bg-card p-5">
                  <div className="mb-4 flex items-center justify-between">
                    <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800">Strategy contribution &amp; performance</h3>
                    <span className="text-xs text-slate-400">P&amp;L per strategy on this day</span>
                  </div>
                  {byStrategy.length ? (
                    <div className="space-y-3">
                      {byStrategy.map((g) => (
                        <div key={g.strategy} className="rounded-lg border border-slate-100 bg-slate-50/50 p-3">
                          <div className="mb-1 flex items-center justify-between text-xs">
                            <span className="font-bold text-slate-800">{titleCase(g.strategy)}</span>
                            <Pnl value={g.pnl} />
                          </div>
                          <div className="my-1.5 h-1.5 overflow-hidden rounded-full bg-slate-200">
                            <div className={cn("h-full rounded-full", g.pnl >= 0 ? "bg-profit" : "bg-loss")} style={{ width: `${Math.max(8, (100 * Math.abs(g.pnl)) / maxAbs)}%` }} />
                          </div>
                          <div className="flex justify-between text-[11px] text-slate-400">
                            <span>Trades {g.trades} ({g.wins}W / {g.trades - g.wins}L · {pct((100 * g.wins) / g.trades, 0)} win)</span>
                            <span>Fees {usd(g.fees, 4)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="py-3 text-center text-xs text-slate-400">No structure closed on this date.</div>
                  )}
                </div>
              </TabsContent>

              <TabsContent value="timeline" className="space-y-6">
                <div className="overflow-hidden rounded-xl border bg-card">
                  <div className="flex items-center justify-between border-b bg-slate-50 px-5 py-3">
                    <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800"><Clock className="size-4 text-primary" /> What happened, in order</h3>
                    {data.events.length >= 300 && <span className="text-xs text-slate-400">the latest 300 events</span>}
                  </div>
                  <div className="p-4 text-xs">
                    {events.length ? (
                      <div className="space-y-1.5">
                        {events.map((e) => (
                          <div key={e.id} className="flex items-start gap-2">
                            <span className="num shrink-0 text-slate-400">{time(e.ts)}</span>
                            <Badge variant={LEVEL_TONE[e.level]} className="shrink-0">{e.type}</Badge>
                            <span className="flex-1 text-slate-700">{e.message}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="py-4 text-center text-slate-400">No trades, orders or alerts on this day.</div>
                    )}
                  </div>
                </div>
                <div className="overflow-hidden rounded-xl border bg-card">
                  <div className="border-b bg-slate-50 px-5 py-3">
                    <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800"><Ban className="size-4 text-amber-500" /> Why the auto-trader stood aside</h3>
                  </div>
                  <div className="p-4 text-xs">
                    {skips.length ? (
                      <table className="w-full">
                        <thead>
                          <tr className="text-left text-slate-400">
                            <th className="py-1 pr-3">Count</th>
                            <th className="pr-3">Reason</th>
                            <th className="pr-3">Underlyings</th>
                            <th>Between</th>
                          </tr>
                        </thead>
                        <tbody>
                          {skips.map((g) => (
                            <tr key={g.reason} className="border-t border-slate-100 align-top">
                              <td className="num py-1.5 pr-3 font-bold">{g.count}</td>
                              <td className="pr-3 text-slate-700">{g.reason}</td>
                              <td className="pr-3 text-slate-500">{g.underlyings.join(", ")}</td>
                              <td className="num whitespace-nowrap text-slate-400">{time(g.first)} – {time(g.last)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <div className="py-4 text-center text-slate-400">The auto-trader skipped nothing on this day.</div>
                    )}
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="orders">
                <div className="overflow-hidden rounded-xl border bg-card">
                  <div className="flex items-center justify-between border-b bg-slate-50 px-5 py-3">
                    <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800"><Send className="size-4 text-slate-500" /> Order routing &amp; execution</h3>
                    <span className="text-xs text-slate-400">{orders.length} orders recorded</span>
                  </div>
                  {orders.length ? (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Time</TableHead>
                          <TableHead>Broker</TableHead>
                          <TableHead>Purpose</TableHead>
                          <TableHead>Contract</TableHead>
                          <TableHead>Side</TableHead>
                          <TableHead className="text-right">Size</TableHead>
                          <TableHead className="text-right">Limit · fill</TableHead>
                          <TableHead className="text-right">Fee</TableHead>
                          <TableHead className="text-right">Status</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {orders.map((o) => (
                          <TableRow key={o.client_order_id}>
                            <TableCell className="num text-xs text-slate-400">{time(o.created_at)}</TableCell>
                            <TableCell>{o.broker}</TableCell>
                            <TableCell>{o.purpose}</TableCell>
                            <TableCell className="num font-medium">{o.symbol}</TableCell>
                            <TableCell className={cn("font-semibold uppercase", o.side === "buy" ? "text-profit" : "text-loss")}>{o.side}</TableCell>
                            <TableCell className="num text-right">{o.filled_size && o.filled_size !== o.size ? `${o.filled_size}/${o.size}` : o.size}</TableCell>
                            <TableCell className="num text-right text-xs">{price(o.limit_price)} · {price(o.fill_price)}</TableCell>
                            <TableCell className="num text-right text-slate-400">{usd(o.fee, 4)}</TableCell>
                            <TableCell className="text-right"><Badge variant={ORDER_TONE[o.status]} title={o.error || undefined}>{o.status}</Badge></TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  ) : (
                    <Empty title="No orders on this day" />
                  )}
                </div>
              </TabsContent>

              <TabsContent value="system">
                <div className="overflow-hidden rounded-xl border bg-card">
                  <div className="flex items-center justify-between border-b bg-slate-50 px-5 py-3">
                    <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800"><AlertTriangle className="size-4 text-amber-500" /> Operational errors &amp; warning events</h3>
                    <Badge variant={problems.length ? "warn" : "profit"}>{problems.length} events</Badge>
                  </div>
                  <div className="p-4 text-xs">
                    {problems.length ? (
                      <div className="space-y-2">
                        {problems.map((e) => (
                          <div key={e.id} className="flex items-start gap-2 rounded border bg-slate-50 p-2.5">
                            <Badge variant={LEVEL_TONE[e.level]} className="num">{e.type}</Badge>
                            <span className="num text-slate-400">{time(e.ts)}</span>
                            <span className="flex-1 break-all text-slate-700">{e.message}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="py-4 text-center text-slate-400">No errors or guard alerts recorded on this day.</div>
                    )}
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="trades">
                <div className="overflow-hidden rounded-xl border bg-card">
                  {closed.length ? (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Closed</TableHead>
                          <TableHead>Structure</TableHead>
                          <TableHead>Strategy</TableHead>
                          <TableHead>Broker</TableHead>
                          <TableHead className="text-right">Lots</TableHead>
                          <TableHead className="text-right">Premium in · out</TableHead>
                          <TableHead className="text-right">Fees</TableHead>
                          <TableHead className="text-right">Realised P&L</TableHead>
                          <TableHead>Close reason</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {closed.map((s) => (
                          <TableRow key={s.id}>
                            <TableCell className="num text-xs text-slate-400">{time(s.closed_at)}</TableCell>
                            <TableCell className="font-medium">{s.underlying} {s.expiry_label}</TableCell>
                            <TableCell>{s.label}</TableCell>
                            <TableCell>{s.broker}</TableCell>
                            <TableCell className="num text-right">{s.qty}</TableCell>
                            <TableCell className="num text-right text-xs">{signed(s.entry_cash, 4)} · {signed(s.exit_cash, 4)}</TableCell>
                            <TableCell className="num text-right text-slate-400">{usd(s.fees, 4)}</TableCell>
                            <TableCell className="text-right"><Pnl value={s.pnl} /></TableCell>
                            <TableCell className="max-w-64 truncate text-slate-500" title={s.close_reason}>{s.close_reason || "—"}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  ) : (
                    <Empty title="No closed trades recorded" />
                  )}
                </div>
              </TabsContent>
            </div>
          </Tabs>
        )}
      </DialogContent>
    </Dialog>
  );
}
