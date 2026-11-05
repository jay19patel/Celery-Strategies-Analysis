"use client";

// Where a broker's capital is right now: what each kind of open position ties up, and what is
// still free, as one bar and the numbers behind it.

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { usd } from "@/lib/format";
import type { Account, Structure } from "@/lib/types";

const GROUPS = [
  { key: "perp", label: "Perpetuals", color: "#6366f1" },
  { key: "strategy", label: "Option strategies", color: "#0ea5e9" },
  { key: "zerohero", label: "Zero-hero", color: "#f59e0b" },
  { key: "single", label: "Single options", color: "#a855f7" },
] as const;

type Group = (typeof GROUPS)[number]["key"];

function groupOf(s: Structure): Group {
  if (s.instrument === "perp") return "perp";
  if (s.strategy === "zero_hero" || s.strategy === "expiry_decay") return "zerohero";
  return s.legs.length === 1 ? "single" : "strategy";
}

/** What a position ties up: its margin, or for a bought option the premium paid. */
function capital(s: Structure): number {
  return s.margin > 0 ? s.margin : Math.max(0, -s.entry_cash);
}

export function CapitalSplit({ account, open }: { account: Account; open: Structure[] }) {
  const total = account.equity;
  if (!(total > 0)) return null;
  const used = Object.fromEntries(GROUPS.map((g) => [g.key, { amount: 0, count: 0 }])) as Record<Group, { amount: number; count: number }>;
  for (const s of open) {
    const g = used[groupOf(s)];
    g.amount += capital(s);
    g.count += 1;
  }
  const inUse = GROUPS.reduce((sum, g) => sum + used[g.key].amount, 0);
  const free = Math.max(0, total - inUse);
  const rows = [
    ...GROUPS.map((g) => ({ ...g, ...used[g.key] })),
    { key: "free", label: "Free", color: "var(--chart-grid)", amount: free, count: 0 },
  ];
  const share = (v: number) => (100 * v) / Math.max(total, inUse);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Capital in use</CardTitle>
        <span className="text-[11.5px] text-muted-foreground">
          {usd(inUse, 2)} of {usd(total, 2)} equity ({share(inUse).toFixed(1)}%) · the broker reports {usd(account.margin_used, 2)} margin used
        </span>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex h-4 w-full overflow-hidden rounded-full bg-secondary" role="img" aria-label="capital split">
          {rows.map((r) =>
            r.amount > 0 ? (
              <div key={r.key} title={`${r.label}: ${usd(r.amount, 2)} (${share(r.amount).toFixed(1)}%)`} style={{ width: `${share(r.amount)}%`, background: r.color }} />
            ) : null,
          )}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {rows.map((r) => (
            <div key={r.key} className="rounded-lg bg-secondary/60 px-2.5 py-2">
              <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <span className="size-2 rounded-full" style={{ background: r.key === "free" ? "var(--muted-foreground)" : r.color }} />
                {r.label}
              </div>
              <div className="num mt-0.5 text-[14px] font-semibold">{share(r.amount).toFixed(1)}%</div>
              <div className="num text-[11px] text-muted-foreground">
                {usd(r.amount, 2)}
                {r.key !== "free" && ` · ${r.count} open`}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
