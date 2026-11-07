"use client";

import { useState } from "react";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApi } from "@/lib/live";
import type { Broker, Funnel } from "@/lib/types";

const STEPS: { key: keyof Funnel; label: string; hint: string }[] = [
  { key: "signals", label: "Signals", hint: "directional signals and auto/zero-hero cycles that reached a broker" },
  { key: "passed", label: "Filtered", hint: "passed every rule and gate" },
  { key: "orders", label: "Orders", hint: "structures sent to this broker" },
  { key: "filled", label: "Filled", hint: "opened in full" },
  { key: "profitable", label: "Profitable", hint: "closed in profit" },
];

const WINDOWS = { "7": "7 days", "30": "30 days", "0": "All" } as const;

// Whole minutes, so the key is stable between renders.
const key = (broker: Broker, since: number, until: number) => `/portfolio/funnel?broker=${broker}&since=${since}&until=${until}`;

/** From what the rules said to what paid, with the window before in grey. */
export function SignalFlow({ broker }: { broker: Broker }) {
  const [days, setDays] = useState<keyof typeof WINDOWS>("7");
  const [now] = useState(() => Math.floor(Date.now() / 60_000) * 60);
  const span = Number(days) * 86_400;
  const since = span ? now - span : 0;
  const cur = useApi<Funnel>(key(broker, since, now), { refreshInterval: 30_000 });
  const prev = useApi<Funnel>(span ? key(broker, since - span, since) : null);
  const f = cur.data;
  const top = f ? Math.max(1, f.signals, f.orders) : 1;
  return (
    <div className="space-y-4">
      <Tabs value={days} onValueChange={(v) => setDays(v as keyof typeof WINDOWS)}>
        <TabsList>
          {Object.entries(WINDOWS).map(([v, label]) => <TabsTrigger key={v} value={v}>{label}</TabsTrigger>)}
        </TabsList>
      </Tabs>
      {cur.error ? (
        <p className="text-[13px] text-loss">{cur.error instanceof Error ? cur.error.message : String(cur.error)}</p>
      ) : !f ? (
        <div className="h-20" />
      ) : (
        <ol className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {STEPS.map((s, i) => {
            const n = f[s.key] as number;
            const before = i ? (f[STEPS[i - 1].key] as number) : 0;
            return (
              <li key={s.key} className="min-w-0" title={s.hint}>
                <div className="text-[12px] text-muted-foreground">{s.label}</div>
                <div className="mt-0.5 flex items-baseline gap-2">
                  <span className="num text-[20px] font-semibold">{n}</span>
                  {prev.data && <span className="num text-[11px] text-muted-foreground">prev {prev.data[s.key] as number}</span>}
                </div>
                <div className="mt-1.5 h-1 rounded-full bg-border">
                  <div className="h-1 rounded-full bg-primary" style={{ width: `${(100 * n) / top}%` }} />
                </div>
                {i > 0 && <div className="num mt-1 text-[11px] text-muted-foreground">{before ? `${Math.round((100 * n) / before)}% of ${STEPS[i - 1].label.toLowerCase()}` : "—"}</div>}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
