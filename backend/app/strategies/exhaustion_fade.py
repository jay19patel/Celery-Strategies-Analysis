"""Exhaustion fade: a climax at one of the day's levels (value edge, POC, a gamma wall or the flip)
that could not hold — fade it back to the point of control."""

from __future__ import annotations

from app.strategies._auction import STOP_BUFFER_ATR, AuctionParams, fresh, near, stop_buffer, trade
from app.strategies.base import Context, Decision, Strategy, bracket, none


class ExhaustionFade(Strategy):
    name = "exhaustion_fade"
    label = "Exhaustion fade"
    summary = "A climax at one of the day's levels that could not hold: fade it back to the point of control."
    stop_rule = f"past the climax extreme, plus {STOP_BUFFER_ATR:g} ATR"
    target_rule = "yesterday's point of control, else the tape's own, else 2x the risk"
    interval = "5m"
    lookback = 120
    default_on = True
    wants_flow = True
    wants_bias = True
    Params = AuctionParams

    def decide(self, ctx: Context) -> Decision:
        f, b = ctx.flow or {}, ctx.bias or {}
        if not f.get("ready"):
            return none(f.get("note") or "no tape yet")
        if not b.get("ready"):
            return none(b.get("note") or "no day bias yet")
        exhausted = fresh(ctx, "exhaustion")
        if not exhausted:
            return none("no exhaustion on the last bars")
        event = exhausted[-1]
        at = near(event["price"], b["levels"])
        if at is None:
            return none(f"{event['detail']}, but not at one of the day's levels")
        side = 1 if event["side"] == "bullish" else -1
        entry = ctx.candles[-1].close
        stop = event["price"] - side * stop_buffer(ctx)
        targets = [b["levels"].get("poc"), (f.get("profile") or {}).get("poc")]
        target = next((float(t) for t in targets if isinstance(t, int | float) and (t - entry) * side > 0), bracket(side, entry, stop, 2.0))
        return trade(ctx, side, stop, target, f"{event['detail']} at {at[0].replace('_', ' ')} {at[1]:,.2f}", self.p.min_rr)
