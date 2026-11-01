import type { ReactNode } from "react";
import { AlertTriangle, Inbox } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function Empty({ title, children, className }: { title: string; children?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-1.5 px-4 py-10 text-center", className)}>
      <div className="mb-1 grid size-11 place-items-center rounded-full bg-secondary">
        <Inbox className="size-5 text-muted-foreground" />
      </div>
      <div className="text-[15px] font-semibold text-foreground">{title}</div>
      {children && <div className="max-w-md text-[13px] leading-relaxed text-muted-foreground">{children}</div>}
    </div>
  );
}

export function LoadError({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className={cn("flex items-start gap-2 rounded-xl bg-loss-soft px-3.5 py-2.5 text-[13px] text-loss", className)}>
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

export function Loading({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full rounded-xl" />
      ))}
    </div>
  );
}

export function Notice({ tone = "info", children, className }: { tone?: "info" | "warn" | "loss"; children: ReactNode; className?: string }) {
  const styles = {
    info: "bg-accent text-accent-foreground",
    warn: "bg-warn-soft text-amber-900",
    loss: "bg-loss-soft text-loss",
  }[tone];
  return <div className={cn("rounded-xl px-3.5 py-2.5 text-[13px] leading-relaxed", styles, className)}>{children}</div>;
}
