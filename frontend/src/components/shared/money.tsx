import { pnlClass, signed, usd } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A P&L figure: signed and coloured. */
export function Pnl({ value, digits = 2, className }: { value: number | null | undefined; digits?: number; className?: string }) {
  return <span className={cn("num font-semibold", pnlClass(value), className)}>{signed(value, digits)}</span>;
}

/** Credit or debit, said in words rather than a sign. */
export function Premium({ value, className }: { value: number; className?: string }) {
  return (
    <span className={cn("num font-semibold", value >= 0 ? "text-profit" : "text-loss", className)}>
      {usd(Math.abs(value), 4)} <span className="text-[11px] font-medium">{value >= 0 ? "credit" : "debit"}</span>
    </span>
  );
}
