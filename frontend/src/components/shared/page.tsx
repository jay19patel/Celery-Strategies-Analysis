import type { ReactNode } from "react";

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-2 sm:gap-3">
      <div className="min-w-0">
        <h1 className="text-[17px] sm:text-lg font-bold tracking-tight text-slate-900">{title}</h1>
        {description && <p className="mt-0.5 text-[12px] sm:text-[13px] text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 overflow-x-auto no-scrollbar py-0.5">{actions}</div>}
    </div>
  );
}
