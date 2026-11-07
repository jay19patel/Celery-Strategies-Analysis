import { describe, expect, it } from "vitest";

import { buildInsights } from "@/components/performance/derive";
import type { Stats } from "@/lib/types";

const summary = { trades: 0, wins: 0, losses: 0, win_rate: null, pnl: 0, gross_pnl: 0, fees: 0, avg_win: null, avg_loss: null, profit_factor: null, expectancy: null, best: null, worst: null, avg_hold_hours: null };
const stats = (over: Partial<Stats>): Stats => ({
  overall: { ...summary, max_drawdown: 0, trades: 20, fees: 1, gross_pnl: 100 }, by_strategy: [], by_source: [], by_broker: [], curve: [], daily: {}, ...over,
});

describe("buildInsights", () => {
  it("names the strategy carrying the book and the one losing, ignoring ones with too few trades", () => {
    const out = buildInsights(stats({ by_strategy: [
      { ...summary, strategy: "iron_condor", trades: 8, pnl: 40, profit_factor: 2.1 },
      { ...summary, strategy: "short_strangle", trades: 6, pnl: -15, win_rate: 33 },
      { ...summary, strategy: "too_few", trades: 2, pnl: 999 },
    ] }));
    expect(out.map((i) => i.title)).toEqual(["Iron Condor carries the book", "Short Strangle is losing"]);
  });
  it("flags fees, compares score buckets, and gives three at most", () => {
    const out = buildInsights(stats({
      overall: { ...summary, max_drawdown: 0, trades: 20, fees: 40, gross_pnl: 100 },
      by_strategy: [{ ...summary, strategy: "a", trades: 9, pnl: 5 }, { ...summary, strategy: "b", trades: 9, pnl: -5 }],
      by_score: [{ ...summary, score: "50-59", trades: 5, expectancy: -1 }, { ...summary, score: "manual", trades: 9, expectancy: 9 }, { ...summary, score: "80-89", trades: 6, expectancy: 2 }],
    }));
    expect(out).toHaveLength(3);
    expect(out[2].title).toBe("Fees eat the edge");
  });
  it("says nothing before the first closed trade", () => {
    expect(buildInsights(stats({ overall: { ...summary, max_drawdown: 0 } }))).toEqual([]);
  });
});
