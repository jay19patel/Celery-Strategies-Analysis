import type { ReactNode } from "react";

/** An iOS large title. The header watches `data-large-title` and shows the small title once this scrolls away. */
export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
      <div className="min-w-0">
        <h1 data-large-title className="text-[30px] font-bold leading-tight tracking-[-0.025em] text-foreground sm:text-[28px]">
          {title}
        </h1>
        {description && <p className="mt-1 max-w-3xl text-[13px] leading-relaxed text-muted-foreground sm:text-sm">{description}</p>}
      </div>
      {actions && <div className="-mx-4 flex items-center gap-2 overflow-x-auto px-4 py-0.5 no-scrollbar sm:mx-0 sm:px-0">{actions}</div>}
    </div>
  );
}

/** A heading over a group of figures; `live` marks figures that move while trades are open. */
export function SectionLabel({ title, hint, live }: { title: string; hint?: ReactNode; live?: boolean }) {
  return (
    <div className="-mb-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 px-1">
      <h2 className="text-[17px] font-semibold tracking-[-0.02em]">{title}</h2>
      {live && (
        <span className="inline-flex items-center gap-1 rounded-full bg-profit-soft px-2 py-0.5 text-[11px] font-semibold text-profit">
          <span className="size-1.5 rounded-full bg-profit" /> Live
        </span>
      )}
      {hint && <span className="text-[13px] text-muted-foreground">{hint}</span>}
    </div>
  );
}
