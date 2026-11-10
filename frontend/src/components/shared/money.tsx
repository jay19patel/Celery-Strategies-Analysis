import { ArrowDownRight, ArrowUpRight } from "lucide-react";

import { Rolling } from "@/components/shared/rolling";
import { arrowSigned, pnlClass, usd } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A P&L figure: signed, coloured, and a small arrow sized to the digits. */
export function Pnl({ value, digits = 2, className }: { value: number | null | undefined; digits?: number; className?: string }) {
  const v = value ?? 0;
  const Arrow = v > 0 ? ArrowUpRight : v < 0 ? ArrowDownRight : null;
  return (
    <span className={cn("num inline-flex items-center gap-0.5 whitespace-nowrap font-semibold", pnlClass(value), className)}>
      {Arrow && value != null && <Arrow aria-hidden className="size-[0.85em] shrink-0" strokeWidth={2.5} />}
      <Rolling text={arrowSigned(value, digits)} />
    </span>
  );
}

/** Credit or debit, said in words rather than a sign. */
export function Premium({ value, className }: { value: number; className?: string }) {
  return (
    <span className={cn("num font-semibold", value >= 0 ? "text-profit" : "text-loss", className)}>
      {usd(Math.abs(value), 4)} <span className="text-[11px] font-medium">{value >= 0 ? "credit" : "debit"}</span>
    </span>
  );
}
