import { describe, expect, it } from "vitest";

import { backoff } from "@/lib/api";
import { arrowSigned } from "@/lib/format";

describe("arrowSigned", () => {
  it("says the sign with an arrow and a true minus, so colour is never the only cue", () => {
    expect(arrowSigned(12.3)).toBe("▲ +12.30");
    expect(arrowSigned(-4.1)).toBe("▼ −4.10");
    expect(arrowSigned(0)).toBe("0.00");
    expect(arrowSigned(null)).toBe("—");
  });
});

describe("backoff", () => {
  it("doubles from 1s to 30s, scaled by 50-100% so dashboards do not reconnect in step", () => {
    expect(backoff(0, () => 1)).toBe(1000);
    expect(backoff(0, () => 0)).toBe(500);
    expect(backoff(3, () => 1)).toBe(8000);
    expect(backoff(20, () => 1)).toBe(30_000);
  });
});
