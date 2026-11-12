"""Sweep + absorption: stops under a pool were taken (a sweep) and the tape shows the aggressors were
absorbed right there — the passive side won. Long (or the mirror, short)."""

from __future__ import annotations

from app.strategies._auction import NEAR_PCT, AuctionParams, fresh, stop_buffer, trade
from app.strategies.base import Context, Decision, Strategy, bracket, next_level, none


class SweepAbsorption(Strategy):
    name = "sweep_absorption"
    version = 2  # v2: a stop inside the costs is refused
    label = "Sweep + absorption"
    summary = "A liquidity pool was swept and the tape shows the aggressors absorbed right there: trade the reversal."
    stop_rule = "past the sweep's extreme, plus 0.25 ATR"
    target_rule = "the next level the market desk sees, else 2x the risk"
    interval = "5m"
    lookback = 120
    default_on = True
    wants_flow = True
    wants_structure = True
    Params = AuctionParams

    def decide(self, ctx: Context) -> Decision:
        if not (ctx.flow or {}).get("ready"):
            return none((ctx.flow or {}).get("note") or "no tape yet")
        absorbed = fresh(ctx, "absorption")
        if not absorbed:
            return none("no absorption on the last bars")
        sweeps = ((ctx.structure or {}).get("liquidity") or {}).get("sweeps") or []
        recent = [s for s in sweeps if s.get("bar_time", 0) >= ctx.candles[-1].time - 7_200]
        buffer = stop_buffer(ctx)
        entry = ctx.candles[-1].close
        for event in reversed(absorbed):
            want = "low" if event["side"] == "bullish" else "high"
            match = next((s for s in recent if s["direction"] == want and abs(s["extreme"] / event["price"] - 1) * 100 <= NEAR_PCT), None)
            if match is None:
                continue
            side = 1 if event["side"] == "bullish" else -1
            stop = (min if side > 0 else max)(match["extreme"], event["price"]) - side * buffer
            level, what = next_level(ctx.structure, side, entry)
            target = level if level else bracket(side, entry, stop, 2.0)
            why = f"{want}s swept at {match['level']:,.2f} and {event['detail']}{f'; target {what}' if level else ''}"
            return trade(ctx, side, stop, target, why, self.p.min_rr)
        return none("absorption on the tape, but not at a level that was just swept")
