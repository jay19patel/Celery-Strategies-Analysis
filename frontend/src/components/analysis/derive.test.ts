import { describe, expect, it } from "vitest";

import { breakdown, daily, filterTrades, monthGrid, shiftMonth, summarise, when } from "@/components/analysis/derive";
import type { Structure } from "@/lib/types";

// 2026-11-02 is a Monday. 10:00 UTC is 15:30 in Asia/Kolkata.
const MON_10 = Date.UTC(2026, 10, 2, 10) / 1000;
const t = (over: Partial<Structure>): Structure =>
  ({ id: Math.random().toString(), status: "closed", broker: "paper", strategy: "iron_condor", underlying: "BTC", kind: "credit", pnl: 1, closed_at: MON_10, opened_at: MON_10 - 3600, ...over }) as Structure;

const rows = [
  t({ pnl: 10 }),
  t({ pnl: -4, strategy: "trend_4h_short", kind: "short", underlying: "ETH", closed_at: MON_10 + 86_400 }),
  t({ pnl: -6, broker: "delta", closed_at: MON_10 + 2 * 86_400 }),
];

describe("analysis", () => {
  it("reads days and hours in the trading timezone", () => {
    expect(when(MON_10, "Asia/Kolkata")).toEqual({ day: "2026-11-02", hour: 15, weekday: 0 });
    expect(when(Date.UTC(2026, 10, 2, 20) / 1000, "Asia/Kolkata").day).toBe("2026-11-03"); // past midnight IST
  });

  it("filters by broker, strategy, symbol, side and range", () => {
    const f = { broker: "", strategies: [], symbols: [], sides: [], days: 0 };
    expect(filterTrades(rows, f)).toHaveLength(3);
    expect(filterTrades(rows, { ...f, broker: "paper" })).toHaveLength(2);
    expect(filterTrades(rows, { ...f, sides: ["short"] }).map((s) => s.pnl)).toEqual([-4]);
    expect(filterTrades(rows, { ...f, symbols: ["BTC"], strategies: ["iron_condor"] })).toHaveLength(2);
    expect(filterTrades(rows, { ...f, days: 1 }, MON_10 + 2 * 86_400 + 60)).toHaveLength(1);
  });

  it("summarises, names the best and worst day, and breaks down by a key", () => {
    const s = summarise(rows, "UTC");
    expect(s.pnl).toBe(0);
    expect(s.winRate).toBeCloseTo(100 / 3);
    expect(s.profitFactor).toBe(1);
    expect(s.bestDay).toEqual({ day: "2026-11-02", pnl: 10 });
    expect(s.worstDay).toEqual({ day: "2026-11-04", pnl: -6 });
    expect([s.greenDays, s.redDays]).toEqual([1, 2]);
    expect(breakdown(rows, (r) => r.strategy).map((g) => [g.key, g.pnl])).toEqual([["iron_condor", 4], ["trend_4h_short", -4]]);
    expect(daily(rows, "UTC")).toEqual({ "2026-11-02": 10, "2026-11-03": -4, "2026-11-04": -6 });
  });

  it("lays out a month in Monday-first weeks and steps across years", () => {
    const cells = monthGrid("2026-11"); // 1 Nov 2026 is a Sunday
    expect(cells.slice(0, 7)).toEqual([null, null, null, null, null, null, "2026-11-01"]);
    expect(cells.length % 7).toBe(0);
    expect(cells.filter(Boolean)).toHaveLength(30);
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
  });
});
