import * as React from "react";

import { cn } from "@/lib/utils";

/** `sticky` (the default) keeps the first column in view while a wide table scrolls on a phone. */
function Table({ className, sticky = true, ...props }: React.ComponentProps<"table"> & { sticky?: boolean }) {
  return (
    <div data-slot="table-container" data-sticky={sticky} className="relative w-full overflow-x-auto overscroll-x-contain">
      <table data-slot="table" className={cn("w-full caption-bottom text-[12.5px] sm:text-[13px]", className)} {...props} />
    </div>
  );
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead data-slot="table-header" className={cn("bg-slate-50 [&_tr]:border-b", className)} {...props} />;
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody data-slot="table-body" className={cn("[&_tr:last-child]:border-0", className)} {...props} />;
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return <tr data-slot="table-row" className={cn("border-b border-slate-100 transition-colors hover:bg-slate-50/70 data-[state=selected]:bg-muted", className)} {...props} />;
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn("h-9 whitespace-nowrap px-3 text-left align-middle text-[11.5px] font-medium text-muted-foreground", className)}
      {...props}
    />
  );
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn("whitespace-nowrap px-3 py-2 align-middle", className)} {...props} />;
}

export { Table, TableHeader, TableBody, TableHead, TableRow, TableCell };
