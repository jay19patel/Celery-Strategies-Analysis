"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { Info, OctagonX, Percent, Ruler, ShieldCheck, Timer, Wallet } from "lucide-react";

import { dayKey } from "@/components/performance/derive";
import { SectionLabel } from "@/components/shared/page";
import { Stat } from "@/components/shared/stat";
import { LoadError } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { dateTime, num, pnlClass, signed, signedPct, usd } from "@/lib/format";
import type { Account, Broker, BrokerStatus, SettingsValues, Stats } from "@/lib/types";

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

/** The broker's settled results: what closed trades booked, after every fee. Nothing that moves with
 * the market is here — open positions, their unrealised P&L and charges are on the Positions page. */
export function AccountSection({
  broker,
  account,
  status,
  stats,
  settings,
}: {
  broker: Broker;
  account: Account | { error: string } | undefined;
  status: BrokerStatus | undefined;
  stats: Stats | undefined;
  settings: SettingsValues | undefined;
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
  const realised = stats?.overall.pnl ?? null;
  const closedFees = stats?.overall.fees ?? 0;
  const grossPnl = stats?.overall.gross_pnl ?? (realised !== null ? realised + closedFees : null);
  const tz = String(settings?.day_timezone ?? "UTC");
  const today = stats?.daily[dayKey(tz)] ?? 0;
  const limitPct = Number(settings?.daily_loss_limit_pct ?? 0);

  return (
    <>
      <SectionLabel title="Realised" hint="closed trades only, after every fee · open positions are on the Positions page" />
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 sm:gap-3 2xl:grid-cols-6">
        {start !== null && <Stat label="Starting balance" value={usd(start)} hint={account.currency} />}
        <Stat label="Net realised P&L" value={signed(realised, 4)} valueClass={pnlClass(realised)} hint="after all fees" />
        {start !== null && (
          <Stat
            label="Realised balance"
            value={usd(start + (realised ?? 0))}
            hint={realised !== null && start ? `${signedPct((100 * realised) / start)} on the start` : undefined}
          />
        )}
        <Stat label="Realised today" value={signed(today, 4)} valueClass={pnlClass(today)} hint={tz} />
        <Stat label="Gross realised" value={signed(grossPnl, 4)} valueClass={pnlClass(grossPnl)} hint="before fees" />
        <Stat label="Charges paid" value={usd(closedFees, 4)} valueClass={closedFees ? "text-warn" : undefined} hint="on closed trades" />
      </div>

      <div className="grid gap-4 sm:gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle><Info className="size-4 text-slate-400" /> Account details</CardTitle>
          </CardHeader>
          <CardContent className="py-1">
            {[
              ["Broker", broker === "paper" ? "Paper (simulated)" : `Delta Exchange (${status?.env ?? "—"})`],
              ["Currency", account.currency],
              ...(start !== null ? [["Starting balance", usd(start)]] : []),
              ...(grossPnl !== null ? [["Gross realised P&L", signed(grossPnl, 4)]] : []),
              ["Charges on closed trades", usd(closedFees, 4)],
              ...(realised !== null ? [["Net realised P&L", signed(realised, 4)]] : []),
              ...(typeof x.created_at === "number" ? [["Account opened", dateTime(x.created_at)]] : []),
              ["Auto-trading", status?.auto ? "on" : "off"],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3 border-b border-slate-100 py-2 text-[13px] last:border-0">
                <span className="shrink-0 text-muted-foreground">{k}</span>
                <span className="num min-w-0 text-right font-medium">{v}</span>
              </div>
            ))}
          </CardContent>
        </Card>

        <div className="grid gap-4 sm:gap-5">
          <Card>
            <CardHeader>
              <CardTitle><ShieldCheck className="size-4 text-slate-400" /> Protection rules</CardTitle>
              <Button asChild variant="outline" size="xs"><Link href="/settings">Edit</Link></Button>
            </CardHeader>
            <CardContent className="space-y-3 text-[12.5px]">
              {settings ? (
                <>
                  <Rule icon={<Ruler />} title={<>Credit structures <Badge variant="profit">TP {String(settings.credit_take_profit_pct)}%</Badge> <Badge variant="loss">SL {String(settings.credit_stop_loss_pct)}%</Badge></>}>
                    Take profit at a share of the maximum profit; stop out when the loss reaches a share of the credit received — measured on what closing would actually book, fees included, and held for {String(settings.stop_confirm_checks ?? 3)} checks in a row unless the spot is already past a breakeven.
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
