"use client";

// The expiry-day scan. It polls while its window is open and goes quiet when it is not, because
// for twenty of every twenty-four hours there is genuinely nothing to look at and a page that
// pretends otherwise teaches you to ignore it.

import { useState } from "react";
import Link from "next/link";
import { Loader2, Play, Settings2, Zap } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/shared/page";
import { Empty, LoadError, Loading, Notice } from "@/components/shared/states";
import { KV, Stat } from "@/components/shared/stat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CandidateTable } from "@/components/zerohero/candidates";
import { CheckList } from "@/components/zerohero/checks";
import { api } from "@/lib/api";
import { ago, num, pct, price, signed, signedPct, usd } from "@/lib/format";
import { useApi } from "@/lib/live";
import type { ZhDecision, ZhScan, ZhStatus } from "@/lib/types";

const UNDERLYINGS = ["BTC", "ETH"];

function countdown(hours: number | null): string {
  if (hours === null) return "—";
  const total = Math.max(0, Math.round(hours * 60));
  return `${Math.floor(total / 60)}h ${String(total % 60).padStart(2, "0")}m`;
}

function Direction({ scan }: { scan: ZhScan }) {
  const d = scan.trigger?.direction ?? 0;
  if (!d) return <Badge variant="outline">no direction</Badge>;
  return <Badge variant={d > 0 ? "profit" : "loss"}>{d > 0 ? "upside — calls" : "downside — puts"}</Badge>;
}

export default function ZeroHeroPage() {
  const [underlying, setUnderlying] = useState("BTC");
  const [running, setRunning] = useState(false);
  const { data: status, error: statusError } = useApi<ZhStatus>(`/zerohero/status?underlying=${underlying}`, { refreshInterval: 30_000 });
  // Only poll hard while the window is open; outside it the answer cannot change for hours.
  const open = status?.enabled;
  const { data: scan, error, isLoading } = useApi<ZhScan>(`/zerohero/scan?underlying=${underlying}`, {
    refreshInterval: open ? 15_000 : 120_000,
  });
  const { data: decisions } = useApi<ZhDecision[]>(`/zerohero/decisions?underlying=${underlying}&limit=40`, { refreshInterval: 60_000 });

  const runNow = async () => {
    setRunning(true);
    try {
      const out = await api<{ decisions: unknown[]; note?: string }>("/zerohero/run", "POST");
      toast.info(out.note ?? `${out.decisions.length} decision(s) recorded`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const record = status?.record;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Zero-Hero"
        description="One OTM option bought on an expiry-day momentum break, out of a daily budget. Most expire worthless."
        actions={
          <>
            <Tabs value={underlying} onValueChange={setUnderlying}>
              <TabsList>
                {UNDERLYINGS.map((u) => (
                  <TabsTrigger key={u} value={u}>
                    {u}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <Button variant="outline" size="sm" onClick={runNow} disabled={running}>
              {running ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />} Scan now
            </Button>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/settings#zerohero">
                <Settings2 className="size-3.5" /> Settings
              </Link>
            </Button>
          </>
        }
      />

      <LoadError error={statusError ?? error} />

      {status && !status.enabled && (
        <Notice tone="warn">
          Zero-hero is switched off. Turn it on in <Link href="/settings#zerohero" className="underline">Settings</Link>. It starts in{" "}
          <strong>shadow mode</strong>: it scans, chooses and sizes, and sends no orders — which is how the hit rate this strategy
          needs gets measured before anything is bet on it.
        </Notice>
      )}
      {status?.enabled && status.shadow && (
        <Notice tone="info">
          <strong>Shadow mode.</strong> Every scan below is recorded with the size it would have bought, and nothing is sent to a
          broker. {record?.closed ? null : "No closed trade has happened yet, so there is no hit rate to report."}
        </Notice>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Window"
          value={scan?.window_open ? "OPEN" : "closed"}
          valueClass={scan?.window_open ? "text-profit" : "text-muted-foreground"}
          hint={scan?.note}
        />
        <Stat label="To expiry" value={countdown(scan?.hours_left ?? null)} hint={`hold ${num(scan?.hold_minutes, 0)} min to the time stop`} />
        <Stat label="Spot" value={price(scan?.spot)} hint={`realised vol ${pct(scan?.realised_vol, 1, true)}`} />
        <Stat
          label="Candidates"
          value={scan?.candidates.length ?? 0}
          valueClass={scan?.ok ? "text-profit" : undefined}
          hint={scan?.rejected.length ? `${scan.rejected.length} refused` : undefined}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Zap className="size-4" /> Scan gates
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? <Loading /> : scan ? <CheckList checks={scan.gates} /> : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Momentum {scan && <Direction scan={scan} />}</CardTitle>
          </CardHeader>
          <CardContent>
            {scan?.trigger ? (
              <>
                <CheckList checks={scan.trigger.checks} className="mb-2" />
                <KV label="Pulse" value={scan.trigger.pulse_state} />
                <KV label="Estimated flow" value={scan.trigger.flow} />
                <KV label="Fib target" value={scan.trigger.target ? `${price(scan.trigger.target)} (${signedPct(scan.trigger.target_pct)})` : "—"} />
              </>
            ) : (
              <Loading rows={2} />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Positioning</CardTitle>
          </CardHeader>
          <CardContent>
            {scan?.positioning ? (
              <>
                <Notice tone={scan.positioning.away_from_pin ? "info" : "warn"} className="mb-2">
                  {scan.positioning.detail}
                </Notice>
                <KV label="Max pain" value={price(scan.positioning.max_pain)} />
                <KV label="Call wall" value={price(scan.positioning.call_wall)} />
                <KV label="Put wall" value={price(scan.positioning.put_wall)} />
                <KV label="ATM IV" value={pct(scan.positioning.atm_iv, 1, true)} />
                <KV label="Implied move" value={pct(scan.positioning.implied_move_pct, 2)} />
                <KV label="25Δ skew" value={num(scan.positioning.skew_25d, 4)} />
              </>
            ) : (
              <Loading rows={2} />
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Candidates</CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          {isLoading ? (
            <Loading className="px-6" />
          ) : scan?.candidates.length ? (
            <CandidateTable candidates={scan.candidates} />
          ) : (
            <Empty title="Nothing passed">
              {scan?.note}
              {scan?.rejected.length ? (
                <div className="mt-3 text-left">
                  {scan.rejected.slice(0, 6).map((r) => (
                    <div key={r.symbol} className="py-0.5">
                      <span className="num font-medium">{price(r.strike)}</span> — refused on <strong>{r.why}</strong>: {r.detail}
                    </div>
                  ))}
                </div>
              ) : null}
            </Empty>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Today&apos;s budget</CardTitle>
          </CardHeader>
          <CardContent>
            {status?.budget.map((b) => (
              <div key={b.broker} className="border-b py-2 last:border-0">
                <div className="flex items-center justify-between text-[13px] font-medium">
                  <span className="capitalize">{b.broker}</span>
                  {!b.auto && <Badge variant="outline">auto off</Badge>}
                </div>
                <KV label="Premium paid today" value={usd(b.spent, 2)} />
                <KV label="Attempts used" value={`${b.attempts} of ${b.max_attempts}`} />
              </div>
            ))}
            {status && (
              <p className="mt-2 text-[11.5px] leading-relaxed text-muted-foreground">
                The budget is {num(status.config.zh_daily_budget_pct, 0)}% of equity for the whole DAY, split across the attempts
                the day has left — not per trade. At an 85% loss rate, a fifth of capital on each attempt is ruinous at any hit
                rate this edge could plausibly have.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>The record</CardTitle>
          </CardHeader>
          <CardContent>
            {record ? (
              <>
                <KV label="Scans (30d)" value={num(record.scans, 0)} />
                <KV label="Proposed a trade" value={num(record.proposed, 0)} />
                <KV label="Shadow only" value={num(record.shadowed, 0)} />
                <KV label="Actually opened" value={num(record.opened, 0)} />
                <KV label="Closed" value={num(record.closed, 0)} />
                <KV label="Hit rate" value={record.hit_rate_pct === null ? "no closed trades yet" : pct(record.hit_rate_pct, 1)} />
                <KV label="Premium paid" value={usd(record.premium_paid, 2)} />
                <KV label="P&L" value={record.pnl === null ? "—" : signed(record.pnl, 2)} />
                <p className="mt-2 text-[11.5px] leading-relaxed text-muted-foreground">{record.note}</p>
              </>
            ) : (
              <Loading rows={3} />
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent scans</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {decisions?.length ? (
            decisions.map((d) => (
              <div key={d.id} className="rounded-lg border px-3 py-2 text-[12.5px]">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={d.action === "open" ? "profit" : d.action === "error" ? "loss" : "outline"}>{d.action}</Badge>
                  <span className="text-muted-foreground">{ago(d.ts)}</span>
                  {d.regime?.shadow && <Badge variant="outline">shadow</Badge>}
                </div>
                <div className="mt-1 text-slate-700">{d.reason}</div>
              </div>
            ))
          ) : (
            <Empty title="No scans recorded yet">The scan writes a row every cycle once zero-hero is switched on.</Empty>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
