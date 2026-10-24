import type { ReactNode } from "react";
import { AlertTriangle, Inbox } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function Empty({ title, children, className }: { title: string; children?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-1.5 px-4 py-10 text-center", className)}>
      <Inbox className="size-6 text-slate-300" />
      <div className="text-[13px] font-medium text-slate-600">{title}</div>
      {children && <div className="max-w-md text-xs text-muted-foreground">{children}</div>}
    </div>
  );
}

export function LoadError({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className={cn("flex items-start gap-2 rounded-lg border border-red-200 bg-loss-soft px-3 py-2 text-[13px] text-loss", className)}>
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

export function Loading({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

export function Notice({ tone = "info", children, className }: { tone?: "info" | "warn" | "loss"; children: ReactNode; className?: string }) {
  const styles = {
    info: "border-blue-200 bg-accent text-accent-foreground",
    warn: "border-amber-200 bg-warn-soft text-amber-900",
    loss: "border-red-200 bg-loss-soft text-loss",
  }[tone];
  return <div className={cn("rounded-lg border px-3 py-2 text-[13px] leading-relaxed", styles, className)}>{children}</div>;
}
