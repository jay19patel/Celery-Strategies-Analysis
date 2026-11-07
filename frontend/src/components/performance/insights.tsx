import type { Insight } from "@/components/performance/derive";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const TONE = { profit: "bg-profit", loss: "bg-loss", neutral: "bg-muted-foreground" };

/** The rule-based notes from `buildInsights`, side by side. */
export function Insights({ items }: { items: Insight[] }) {
  if (!items.length) return null;
  return (
    <div className="grid gap-2.5 sm:grid-cols-3 sm:gap-3">
      {items.map((i) => (
        <Card key={i.title} className="px-4 py-3">
          <h3 className="flex items-center gap-2 text-[13px] font-semibold">
            <span className={cn("size-1.5 shrink-0 rounded-full", TONE[i.tone])} aria-hidden />
            {i.title}
          </h3>
          <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">{i.body}</p>
        </Card>
      ))}
    </div>
  );
}
