import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function Stat({ label, value, hint, className, valueClass }: { label: ReactNode; value: ReactNode; hint?: ReactNode; className?: string; valueClass?: string }) {
  return (
    <div className={cn("min-w-0 rounded-lg border bg-card px-2.5 py-2 sm:px-3.5 sm:py-3 shadow-2xs", className)}>
      <div className="truncate text-[11px] sm:text-[11.5px] font-medium text-muted-foreground">{label}</div>
      <div className={cn("num mt-0.5 sm:mt-1 truncate text-[15px] sm:text-[17px] font-semibold leading-tight", valueClass)}>{value}</div>
      {hint !== undefined && <div className="mt-0.5 truncate text-[10.5px] sm:text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function KV({ label, value, className }: { label: ReactNode; value: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3 py-1 text-[13px]", className)}>
      <span className="text-muted-foreground">{label}</span>
      <span className="num text-right font-medium">{value}</span>
    </div>
  );
}
