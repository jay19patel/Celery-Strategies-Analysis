import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";

interface Paged {
  rows: unknown[];
  total: number;
  page: number;
  pages: number;
  size: number;
}

/** "21–40 of 102" and the page arrows under a server-paged list; page 1 is the newest. */
export function Pager({ data, label, onPage }: { data: Paged; label: string; onPage: (page: number) => void }) {
  const from = (data.page - 1) * data.size + 1;
  const to = from + data.rows.length - 1;
  return (
    <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-2.5 text-xs text-muted-foreground">
      <span className="num">
        {from}–{to} of {data.total}
      </span>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon-sm" className="rounded-full" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)} aria-label={`Newer ${label}`}>
          <ChevronLeft />
        </Button>
        <span className="num min-w-20 text-center">
          Page {data.page} of {data.pages}
        </span>
        <Button variant="ghost" size="icon-sm" className="rounded-full" disabled={data.page >= data.pages} onClick={() => onPage(data.page + 1)} aria-label={`Older ${label}`}>
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}
