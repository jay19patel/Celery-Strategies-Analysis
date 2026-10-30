"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { Info, OctagonX, Percent, Ruler, ShieldCheck, Timer, Wallet } from "lucide-react";
import { toast } from "sonner";

import { dayKey } from "@/components/performance/derive";
import { Confirm } from "@/components/shared/confirm";
import { Pnl } from "@/components/shared/money";
import { Stat } from "@/components/shared/stat";
import { LoadError } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { dateTime, num, pnlClass, signed, signedPct, usd } from "@/lib/format";
import type { Account, Broker, BrokerStatus, SettingsValues, Stats, Structure } from "@/lib/types";
import { cn } from "@/lib/utils";

function Rule({ icon, title, children }: { icon: ReactNode; title: ReactNode; children: ReactNode }) {
  return (
    <div className="flex gap-2.5">
      <span className="mt-0.5 shrink-0 text-slate-400 [&>svg]:size-4">{icon}</span>
      <div>
        <div className="font-medium text-slate-800">{title}</div>
        <div className="text-[11.5px] text-slate-500">{children}</div>
      </div>
    </div>
  );
}

/** The broker's account: what it holds, what today has cost, and the rules guarding it. */
export function AccountSection({
  broker,
  account,
  status,
  open,
  stats,
  settings,
  onResumed,
}: {
  broker: Broker;
  account: Account | { error: string } | undefined;
  status: BrokerStatus | undefined;
  open: Structure[];
  stats: Stats | undefined;
  settings: SettingsValues | undefined;
  onResumed: () => void;
}) {
  if (!account) return null;
  if ("error" in account) {
    return (
      <LoadError
        error={`${broker}: ${account.error}${broker === "delta" ? " — configure Delta in Settings" : ""}`}
      />
    );
  }
  const x = account.extra;
  const start = typeof x.starting_balance === "number" ? x.starting_balance : null;
  const unrealised = open.reduce((a, s) => a + (s.live?.pnl ?? 0), 0);
  const realised = stats?.overall.pnl ?? null;
  const feesPaid = stats?.overall.fees ?? (typeof x.fees_paid === "number" ? x.fees_paid : 0);
  const grossPnl = stats?.overall.gross_pnl ?? (realised !== null ? realised + feesPaid : null);
  const tz = String(settings?.day_timezone ?? "UTC");
  const today = stats?.daily[dayKey(tz)] ?? 0;
  const limitPct = Number(settings?.daily_loss_limit_pct ?? 0);
  // The day's starting equity is not reported; equity less today's booked and open P&L is that
  // figure for every move this dashboard can see.
  const dayStartEquity = account.equity - today - unrealised;
  const loss = Math.max(0, -(today + unrealised));
  const used = limitPct && dayStartEquity > 0 ? Math.min(100, (100 * loss) / ((dayStartEquity * limitPct) / 100)) : 0;

  const resume = async () => {
    try {
      await api("/portfolio/resume", "POST", { broker });
      toast.success(`${broker} resumed`);
      onResumed();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Equity" value={usd(account.equity)} hint={account.currency} />
        <Stat label="Wallet balance" value={usd(account.cash)} hint="cash, before open legs" />
        <Stat label="Available" value={usd(account.available)} hint="for new margin" />
        <Stat label="Margin used" value={usd(account.margin_used)} />
        <Stat label="Unrealised P&L" value={signed(unrealised, 4)} valueClass={pnlClass(unrealised)} hint={`${open.length} open structure${open.length === 1 ? "" : "s"}`} />
        <Stat label="Total Charges / Fees" value={usd(feesPaid, 4)} valueClass="text-amber-600 dark:text-amber-400" hint="exchange & broker fees" />
        <Stat
          label="Net Realised P&L"
          value={realised === null ? "—" : signed(realised, 4)}
          valueClass={pnlClass(realised)}
          hint={grossPnl !== null ? `Gross: ${signed(grossPnl, 2)}` : (start ? `${signedPct(((account.equity - start) / start) * 100)} since start` : "closed structures")}
        />
      </div>

      <div className="rounded-xl border border-slate-200/80 bg-gradient-to-r from-slate-50 via-white to-slate-50 p-3.5 shadow-2xs dark:border-slate-800 dark:from-slate-900/60 dark:via-slate-900/40 dark:to-slate-900/60">
        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <span className="flex size-6 items-center justify-center rounded-full bg-slate-200/70 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
              <Info className="size-3.5" />
            </span>
            <div>
              <div className="text-xs font-semibold text-slate-800 dark:text-slate-200">
                P&L & Charges Breakdown
              </div>
              <div className="text-[11px] text-muted-foreground">
                Gross Trading Result − Total Charges = Net Realised P&L
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 dark:border-slate-700 dark:bg-slate-800">
              <span className="text-muted-foreground">Gross P&L:</span>
              <span className={cn("num font-semibold", pnlClass(grossPnl))}>
                {grossPnl !== null ? signed(grossPnl, 4) : "—"}
              </span>
            </div>
            <span className="font-bold text-slate-400">−</span>
            <div className="flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50/60 px-2.5 py-1 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
              <span className="text-amber-700 dark:text-amber-400">Charges & Fees:</span>
              <span className="num font-semibold">{usd(feesPaid, 4)}</span>
            </div>
            <span className="font-bold text-slate-400">=</span>
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 dark:border-slate-700 dark:bg-slate-800">
              <span className="text-muted-foreground">Net Realised:</span>
              <span className={cn("num font-semibold", pnlClass(realised))}>
                {realised !== null ? signed(realised, 4) : "—"}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle><Info className="size-4 text-slate-400" /> Account details</CardTitle>
          </CardHeader>
          <CardContent className="py-1">
            {[
              ["Broker", broker === "paper" ? "Paper (simulated)" : `Delta Exchange (${status?.env ?? "—"})`],
              ["Currency", account.currency],
              ...(start !== null ? [["Starting balance", usd(start)]] : []),
              ...(grossPnl !== null ? [["Gross trading P&L", signed(grossPnl, 4)]] : []),
              ["Total fees & charges paid", usd(feesPaid, 4)],
              ...(realised !== null ? [["Net realised P&L", signed(realised, 4)]] : []),
              ...(typeof x.created_at === "number" ? [["Account opened", dateTime(x.created_at)]] : []),
              ["Auto-trading", status?.auto ? "on" : "off"],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3 border-b border-slate-100 py-2 text-[13px] last:border-0">
                <span className="text-muted-foreground">{k}</span>
                <span className="num font-medium">{v}</span>
              </div>
            ))}
            {Array.isArray(x.assets) && (
              <div className="py-2">
                <div className="mb-1 text-[11.5px] text-muted-foreground">Assets</div>
                {(x.assets as { asset: string; balance: number }[]).map((a) => (
                  <div key={a.asset} className="num flex justify-between text-[12.5px]">
                    <span className="font-semibold">{a.asset}</span>
                    <span>{num(a.balance, 6)}</span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-5">
          <Card>
            <CardHeader>
              <CardTitle>
                <OctagonX className="size-4 text-slate-400" /> Today
                <span className="text-xs font-normal text-muted-foreground">how much of the daily loss limit is used</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[11.5px] text-muted-foreground">Realised today</div>
                  <Pnl value={today} className="text-2xl" />
                  <div className="num text-[11px] text-muted-foreground">open {signed(unrealised, 4)}</div>
                </div>
                <div className="text-right">
                  <div className="text-[11.5px] text-muted-foreground">Day started near</div>
                  <div className="num text-sm">{usd(dayStartEquity)}</div>
                </div>
              </div>
              <div>
                <div className="mb-1 flex justify-between text-[11px]">
                  <span className="text-muted-foreground">
                    Daily loss limit {limitPct ? `${num(limitPct, 2)}% · ${usd((dayStartEquity * limitPct) / 100)}` : "off"}
                  </span>
                  <span className={cn("num", used >= 80 ? "text-loss" : "text-muted-foreground")}>{limitPct ? `${num(used, 0)}% used` : ""}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                  <div className={cn("h-full rounded-full", used >= 80 ? "bg-loss" : used >= 50 ? "bg-amber-500" : "bg-profit")} style={{ width: `${limitPct ? used : 0}%` }} />
                </div>
              </div>
              {status?.halted && (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-100 bg-loss-soft p-2.5">
                  <span className="text-[11.5px] text-loss"><b>Halted for today.</b> {status.halted}</span>
                  <Confirm
                    title={`Resume ${broker} today?`}
                    description="New entries are allowed again. Today's limit restarts from the current equity."
                    confirmLabel="Resume trading"
                    destructive
                    onConfirm={resume}
                    trigger={(o) => <Button variant="destructive-outline" size="xs" onClick={o}>Resume</Button>}
                  />
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle><ShieldCheck className="size-4 text-slate-400" /> Protection rules</CardTitle>
              <Button asChild variant="outline" size="xs"><Link href="/settings">Edit</Link></Button>
            </CardHeader>
            <CardContent className="space-y-3 text-[12.5px]">
              {settings ? (
                <>
                  <Rule icon={<Ruler />} title={<>Credit structures <Badge variant="profit">TP {String(settings.credit_take_profit_pct)}%</Badge> <Badge variant="loss">SL {String(settings.credit_stop_loss_pct)}%</Badge></>}>
                    Take profit at a share of the maximum profit; stop out when the loss reaches a share of the credit received — measured on what closing would actually book, fees included.
                  </Rule>
                  <Rule icon={<Ruler />} title={<>Debit structures <Badge variant="profit">TP {String(settings.debit_take_profit_pct)}%</Badge> <Badge variant="loss">SL {String(settings.debit_stop_loss_pct)}%</Badge></>}>
                    Both levels are shares of the debit paid.
                  </Rule>
                  <Rule icon={<Timer />} title={<>Exit before expiry <Badge>{String(settings.exit_before_expiry_minutes)} min</Badge></>}>
                    Settlement and pin risk are not worth the last stretch; anything still open after expiry is settled at intrinsic value.
                  </Rule>
                  <Rule icon={<OctagonX />} title={<>Daily loss limit {limitPct ? <Badge variant="warn">{num(limitPct, 2)}%</Badge> : <Badge>off</Badge>}</>}>
                    Per broker. Hit it and that broker&apos;s structures close and nothing new opens until the next day ({tz}).
                  </Rule>
                  <Rule icon={<Percent />} title={<>Position size <Badge>{String(settings.risk_per_trade_pct)}% per trade</Badge></>}>
                    The auto-trader sizes each structure so its worst case is this share of equity, at most {String(settings.max_lots)} lots and {String(settings.max_open_structures)} open structures per broker.
                  </Rule>
                </>
              ) : (
                <div className="flex items-center gap-2 text-muted-foreground"><Wallet className="size-4" /> Loading…</div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
