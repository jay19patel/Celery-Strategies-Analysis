"use client";

import { Compass } from "lucide-react";

import { Hint } from "@/components/shared/hint";
import { LoadError, Loading } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useApi } from "@/lib/live";
import type { Regime, StrategySpec } from "@/lib/types";
import { cn } from "@/lib/utils";

const LABEL_TONE: Record<string, "profit" | "loss" | "warn" | "primary" | "default"> = {
  "trending up": "profit",
  "trending down": "loss",
  volatile: "warn",
  "quiet range": "primary",
  range: "default",
};

export function RegimeCard({
  underlying,
  expiry,
  strategies,
  minScore,
  selected,
  onPick,
}: {
  underlying: string;
  expiry: number;
  strategies: StrategySpec[];
  minScore: number;
  selected: string;
  onPick: (key: string) => void;
}) {
  const { data, error, isLoading } = useApi<Regime>(`/market/regime?underlying=${underlying}&expiry=${expiry}`, { refreshInterval: 60_000 });
  const ranked = data ? strategies.filter((s) => data.scores[s.key]).sort((a, b) => data.scores[b.key].score - data.scores[a.key].score) : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Compass className="size-4 text-slate-400" /> Market regime
          <Hint>Rule scores from the volatility premium and the trend, each point explained. They rank fit, they are not probabilities.</Hint>
        </CardTitle>
        {data && <Badge variant={LABEL_TONE[data.label] ?? "default"} className="capitalize">{data.label}</Badge>}
      </CardHeader>
      <CardContent className="space-y-4">
        <LoadError error={error} />
        {isLoading && !data && <Loading rows={5} />}
        {data && (
          <>
            <div>
              <div className="mb-1.5 flex justify-between text-[11px] text-muted-foreground">
                <span>Down</span>
                <span className="num font-medium text-slate-700">direction {data.direction > 0 ? "+" : ""}{data.direction.toFixed(2)}</span>
                <span>Up</span>
              </div>
              <div className="relative h-2 rounded-full bg-gradient-to-r from-red-100 via-slate-100 to-green-100">
                <div className="absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-slate-700 shadow" style={{ left: `${(data.direction + 1) * 50}%` }} />
              </div>
              <ul className="mt-2 space-y-0.5 text-[12px] text-muted-foreground">
                {data.direction_parts.map((p) => (
                  <li key={p}>· {p}</li>
                ))}
              </ul>
            </div>
            <div className="space-y-1.5">
              {ranked.map((s) => {
                const sc = data.scores[s.key];
                return (
                  <Tooltip key={s.key}>
                    <TooltipTrigger asChild>
                      <button
                        onClick={() => onPick(s.key)}
                        className={cn("group w-full rounded-md px-2 py-1.5 text-left transition-colors hover:bg-slate-50", selected === s.key && "bg-accent")}
                      >
                        <div className="flex items-center justify-between text-[12.5px]">
                          <span className="font-medium">{s.label}</span>
                          <span className={cn("num font-semibold", sc.score >= minScore ? "text-profit" : "text-slate-400")}>{sc.score}</span>
                        </div>
                        <div className="mt-1 h-1.5 rounded-full bg-slate-100">
                          <div className={cn("h-1.5 rounded-full", sc.score >= minScore ? "bg-profit" : "bg-slate-300")} style={{ width: `${sc.score}%` }} />
                        </div>
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="left">
                      {sc.reasons.length ? sc.reasons.map((r) => <div key={r}>{r}</div>) : "no adjustments"}
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </div>
            {data.notes.map((n) => (
              <p key={n} className="text-[11.5px] text-warn">{n}</p>
            ))}
          </>
        )}
      </CardContent>
    </Card>
  );
}
