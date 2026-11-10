import type { ReactNode } from "react";

import { Rolling } from "@/components/shared/rolling";
import { cn } from "@/lib/utils";

/** One figure in a card. The number is never cut: its size follows the card's own width (a container
 *  query), so a long figure in a narrow column gets smaller instead of losing digits to "…". */
export function Stat({ label, value, hint, className, valueClass }: { label: ReactNode; value: ReactNode; hint?: ReactNode; className?: string; valueClass?: string }) {
  return (
    <div className={cn("@container min-w-0 rounded-2xl bg-card px-3.5 py-3", className)}>
      <div className="text-[12px] font-medium leading-snug text-muted-foreground">{label}</div>
      <div
        className={cn(
          "num mt-1 whitespace-nowrap text-[15px] font-semibold leading-tight tracking-[-0.02em] @[150px]:text-[17px] @[190px]:text-[19px] @[240px]:text-[21px]",
          valueClass,
        )}
      >
        {typeof value === "string" || typeof value === "number" ? <Rolling text={value} /> : value}
      </div>
      {hint !== undefined && <div className="mt-0.5 text-[11.5px] leading-snug text-muted-foreground">{hint}</div>}
    </div>
  );
}

/** A grouped-list row: label on the left, value on the right, a hairline between rows. */
export function KV({ label, value, className }: { label: ReactNode; value: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3 border-b border-separator py-2 text-[13.5px] last:border-0", className)}>
      <span className="text-muted-foreground">{label}</span>
      <span className="num text-right font-medium">{typeof value === "string" || typeof value === "number" ? <Rolling text={value} /> : value}</span>
    </div>
  );
}
