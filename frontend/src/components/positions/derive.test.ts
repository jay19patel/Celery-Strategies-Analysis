import { describe, expect, it } from "vitest";

import { needsAction } from "@/components/positions/derive";
import type { Structure } from "@/lib/types";

function structure(over: Partial<Structure>): Structure {
  return {
    id: "s1", broker: "paper", underlying: "BTC", strategy: "short_strangle", label: "BTC strangle", source: "auto", status: "open",
    instrument: "options", kind: "credit", stop_price: null, target_price: null, reason: "", expiry: 0, expiry_label: "", hours_left: 48,
    qty: 1, entry_spot: null, exit_spot: null, entry_cash: 10, exit_cash: 0, fees: 0, pnl: null, max_profit: 10, max_loss: null, margin: 0,
    take_profit_pct: 50, stop_loss_pct: 100, take_profit_usd: 5, stop_loss_usd: -10, held_to_settlement: false, mark_pnl: null,
    opened_at: 0, closed_at: null, close_reason: "", error: "", decision_id: null, legs: [], live: null, ...over,
  };
}
const at = (pnl: number | null): Partial<Structure> => ({ live: { pnl, pnl_mid: pnl, missing: [], spot: null } });

describe("needsAction", () => {
  it("ranks a stuck structure first and leaves a capped credit to settle on its own", () => {
    const out = needsAction(
      [structure({ id: "a", ...at(-9) }), structure({ id: "b", status: "error", error: "timeout" }), structure({ id: "c", held_to_settlement: true, hours_left: 1 })],
      [],
    );
    expect(out.map((i) => i.id)).toEqual(["b:stuck", "a:near_stop"]);
  });
  it("sees a target, an expiry, a perpetual near its stop and a strategy error", () => {
    expect(needsAction([structure(at(4.5))], [])[0].kind).toBe("near_target");
    expect(needsAction([structure({ hours_left: 3, ...at(0) })], [])[0].detail).toBe("expires in 3.0h");
    const perp = structure({ instrument: "perp", kind: "long", stop_price: 90, target_price: 120, legs: [{ entry_price: 100, mark: 91 } as Structure["legs"][number]] });
    expect(needsAction([perp], [])[0].detail).toBe("90% of the way to its stop");
    expect(needsAction([], [{ id: "7", source: "Auto", text: "boom" }])).toEqual([{ id: "err:7", kind: "error", title: "Auto", detail: "boom" }]);
  });
  it("says nothing without a price, rather than guess", () => {
    expect(needsAction([structure(at(null))], [])).toEqual([]);
  });
});
