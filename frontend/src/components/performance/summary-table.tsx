import { Pnl } from "@/components/shared/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { hours, num, pct, titleCase } from "@/lib/format";
import type { Summary } from "@/lib/types";

/** One row per group (strategy, source, broker) with its results. */
export function SummaryTable<K extends string>({ rows, keyName, label }: { rows: (Summary & Record<K, string>)[]; keyName: K; label: string }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{label}</TableHead>
          <TableHead className="text-right">Trades</TableHead>
          <TableHead className="text-right">Win rate</TableHead>
          <TableHead className="text-right">P&L</TableHead>
          <TableHead className="text-right">Profit factor</TableHead>
          <TableHead className="text-right">Avg hold</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r[keyName]}>
            <TableCell className="font-medium">{titleCase(r[keyName])}</TableCell>
            <TableCell className="num text-right">{r.trades}</TableCell>
            <TableCell className="num text-right">{pct(r.win_rate)}</TableCell>
            <TableCell className="text-right"><Pnl value={r.pnl} /></TableCell>
            <TableCell className="num text-right">{num(r.profit_factor)}</TableCell>
            <TableCell className="num text-right">{hours(r.avg_hold_hours)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
