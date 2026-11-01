import type { ReactNode } from "react";

import { Rolling } from "@/components/shared/rolling";
import { cn } from "@/lib/utils";

export function Stat({ label, value, hint, className, valueClass }: { label: ReactNode; value: ReactNode; hint?: ReactNode; className?: string; valueClass?: string }) {
  return (
    <div className={cn("min-w-0 rounded-2xl bg-card px-3.5 py-3", className)}>
      <div className="truncate text-[12px] font-medium text-muted-foreground">{label}</div>
      <div className={cn("num mt-1 truncate text-[19px] font-semibold leading-tight tracking-[-0.02em] sm:text-[21px]", valueClass)}>
        {typeof value === "string" || typeof value === "number" ? <Rolling text={value} /> : value}
      </div>
      {hint !== undefined && <div className="mt-0.5 truncate text-[11.5px] text-muted-foreground">{hint}</div>}
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
