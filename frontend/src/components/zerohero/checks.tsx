"use client";

// The dimensions, shown as what they are: a named test, its verdict, and the sentence the backend
// wrote to explain it. The UI never recomputes a verdict — if these disagreed with the scan, the
// page would be lying about why a trade did or did not happen.

import { Check, X } from "lucide-react";

import type { ZhCheck } from "@/lib/types";
import { cn } from "@/lib/utils";

export function CheckRow({ check, className }: { check: ZhCheck; className?: string }) {
  return (
    <div className={cn("flex items-start gap-2 py-1 text-[12.5px]", className)}>
      {check.ok ? <Check className="mt-0.5 size-3.5 shrink-0 text-profit" /> : <X className="mt-0.5 size-3.5 shrink-0 text-loss" />}
      <div className="min-w-0">
        <span className={cn("font-medium", check.ok ? "text-slate-700" : "text-loss")}>{check.name}</span>
        <span className="text-muted-foreground"> — {check.detail}</span>
      </div>
    </div>
  );
}

export function CheckList({ checks, className }: { checks: ZhCheck[]; className?: string }) {
  if (!checks.length) return null;
  return <div className={cn("divide-y", className)}>{checks.map((c) => <CheckRow key={c.name} check={c} />)}</div>;
}
